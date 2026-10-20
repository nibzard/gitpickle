"""Exercise installation replay, strict SSH transport, and configuration isolation."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gitpickle_setup as setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-setup-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / 'gitpickle'
        self.binary.write_bytes(b'verified binary fixture')
        self.output = self.root / 'review'
        self.identity = dict(home=str(self.root / 'home'), uid=os.getuid(), system='Linux', machine='x86_64',
            machine_id=Path('/etc/machine-id').read_text().strip(), python=[3, 11],
            libc=['glibc', '2.36'], user_manager='running',
            dependencies={n: True for n in ['git', 'git-lfs', 'prlimit', 'systemctl']},
            ca_certificates=True, tailscale=dict(installed=True, state='Running', dns='host.example.ts.net.'))
        self.args = ['--target', 'local', '--output', str(self.output), '--binary', str(self.binary), '--port', '32123', '--json']

    def call(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = setup.main(args)
        return code, json.loads(out.getvalue()), err.getvalue()

    def configured_args(self):
        return self.args + ['--configure', '--provider', 'r2', '--endpoint',
            'https://account.r2.cloudflarestorage.com', '--region', 'auto',
            '--bucket', 'private-bucket', '--prefix', 'archive/']

    def credentials(self):
        return dict(GITPICKLE_GITHUB_TOKEN='github-private', GITPICKLE_TOKEN='m' * 40,
                    GITPICKLE_STORAGE_ACCESS_KEY='access-private', GITPICKLE_STORAGE_SECRET_KEY='secret-private')

    def test_guided_runtime_preview_and_resume_without_secret_reentry(self):
        with patch.dict(os.environ, self.credentials()), patch('gitpickle_github.GitHub.repositories',
                return_value={'123': {'alias': 'owner/project'}}), patch.object(setup, 'execute', return_value=self.identity):
            code, result, diagnostic = self.call(self.configured_args())
        self.assertEqual(code, 0)
        for secret in self.credentials().values():
            self.assertNotIn(secret, json.dumps(result) + diagnostic + (self.output / 'plan.json').read_text())
        staged = self.output / 'runtime-inputs.json'
        self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {}, clear=True), patch('builtins.input', side_effect=AssertionError), \
                patch('getpass.getpass', side_effect=AssertionError), patch.object(setup, 'execute', return_value=self.identity):
            self.assertEqual(self.call(self.configured_args() + ['--resume'])[0], 0)
        policy = json.loads(json.loads(staged.read_text())['files']['policy.json'])
        self.assertTrue(all(g['repositories'] == ['github-123'] for g in policy['grants']))
        self.assertTrue(all(g['sources'] == ['github'] for g in policy['grants']))
        self.assertEqual(['github'], result['data']['plan']['runtime']['authorized_sources'])

    def test_configuration_uses_shared_github_credential_provider(self):
        def transport(url, headers):
            self.assertEqual(headers['Authorization'], 'Bearer github-private')
            return 200, {}, b'[{"id":123,"full_name":"owner/project"}]'
        with patch.dict(os.environ, self.credentials()), patch('gitpickle_github.GitHub._transport', side_effect=transport), \
                patch.object(setup, 'execute', return_value=self.identity):
            self.assertEqual(self.call(self.configured_args())[0], 0)
            self.assertEqual(self.call(self.configured_args() + ['--resume', '--apply', '--yes'])[0], 0)

    def test_completed_apply_does_not_repeat_authorization(self):
        def execute(args, source, payload=None):
            return self.identity if source == setup.PROBE else {'configured': True}
        with patch.dict(os.environ, self.credentials()), patch('gitpickle_github.GitHub.repositories',
                return_value={'123': {'alias': 'owner/project'}}), patch.object(setup, 'execute', side_effect=execute):
            self.assertEqual(self.call(self.configured_args())[0], 0)
            self.assertEqual(self.call(self.configured_args() + ['--resume', '--apply', '--yes'])[0], 0)
        with patch('gitpickle_github.GitHub.repositories', side_effect=AssertionError), \
                patch('builtins.input', side_effect=AssertionError), patch.object(setup, 'execute', side_effect=execute):
            self.assertEqual(self.call(self.configured_args() + ['--resume', '--apply'])[0], 0)
            self.assertEqual(self.call(self.configured_args() + ['--resume', '--bucket', 'changed'])[0], 2)

    def test_missing_secret_and_changed_scope_stop_before_installation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(setup, 'execute', return_value=self.identity), \
                patch('getpass.getpass', side_effect=AssertionError):
            self.assertEqual(self.call(self.configured_args())[0], 2)
        with patch.dict(os.environ, self.credentials()), patch('gitpickle_github.GitHub.repositories',
                return_value={'123': {'alias': 'owner/project'}}), patch.object(setup, 'execute', return_value=self.identity):
            self.assertEqual(self.call(self.configured_args())[0], 0)
        with patch('gitpickle_github.GitHub.repositories', return_value={}), patch.object(setup, 'execute', return_value=self.identity) as execute:
            self.assertEqual(self.call(self.configured_args() + ['--resume', '--apply', '--yes'])[0], 2)
            self.assertTrue(all(c.args[1] == setup.PROBE for c in execute.call_args_list))

    def test_runtime_files_created_without_hand_editing_and_preserved(self):
        with patch.dict(os.environ, self.credentials()), patch('gitpickle_github.GitHub.repositories',
                return_value={'123': {'alias': 'owner/project'}}), patch.object(setup, 'execute', return_value=self.identity):
            self.assertEqual(self.call(self.configured_args())[0], 0)
        runtime = json.loads((self.output / 'runtime-inputs.json').read_text())
        home = Path(self.identity['home'])
        home.mkdir()
        helper = self.root / 'storage-check'
        helper.write_text('#!/bin/sh\nexit 0\n')
        helper.chmod(0o700)
        payload = dict(identity=self.identity, files=runtime['files'], executable=str(helper))
        def configure():
            return subprocess.run([sys.executable, '-I', '-c', setup.CONFIGURE],
                input=json.dumps(payload).encode(), capture_output=True, env=dict(os.environ, HOME=str(home)))
        helper.write_text('#!/bin/sh\nexit 1\n')
        self.assertNotEqual(configure().returncode, 0)
        self.assertFalse((home / '.config/gitpickle/policy.json').exists())
        helper.write_text('#!/bin/sh\nexit 0\n')
        self.assertEqual(configure().returncode, 0)
        config = home / '.config/gitpickle'
        for name, content in runtime['files'].items():
            self.assertEqual((config / name).read_text(), content)
            self.assertEqual((config / name).stat().st_mode & 0o777, 0o600)
        # Replay a partial installation without changing stable runtime values.
        (config / 'runtime.env').unlink()
        self.assertEqual(configure().returncode, 0)
        helper.unlink()  # A completed replay needs no credentials or provider calls.
        self.assertEqual(configure().returncode, 0)
        (config / 'policy.json').write_text('existing policy')
        self.assertNotEqual(configure().returncode, 0)
        self.assertEqual((config / 'policy.json').read_text(), 'existing policy')

    def test_noninteractive_requires_arguments_without_prompting(self):
        with patch('sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=AssertionError):
            code, result, _ = self.call(['--json'])
        self.assertEqual(code, 2)
        self.assertEqual(result['error']['code'], 'setup_failed')
        self.assertFalse(self.output.exists())

    def test_preview_resume_and_changed_plan(self):
        with patch.object(setup, 'execute', return_value=self.identity) as execute:
            code, preview, _ = self.call(self.args)
            self.assertEqual(code, 0)
            before = (self.output / 'plan.json').read_bytes()
            self.assertEqual(self.call(self.args + ['--resume'])[0], 0)
            self.assertEqual(before, (self.output / 'plan.json').read_bytes())
            # Repeated previews and resume do not run installation.
            self.assertTrue(all(c.args[1] == setup.PROBE for c in execute.call_args_list))
            self.assertEqual(preview['data']['plan']['tailscale']['state'], 'Running')
            self.binary.write_bytes(b'changed artifact')
            self.assertEqual(self.call(self.args + ['--resume', '--apply', '--yes'])[0], 2)
            self.assertEqual(before, (self.output / 'plan.json').read_bytes())

    def test_apply_requires_review_and_confirmation(self):
        with patch.object(setup, 'execute', return_value=self.identity) as execute:
            self.assertEqual(self.call(self.args + ['--apply', '--yes'])[0], 2)
            self.assertFalse(execute.called)
            self.assertEqual(self.call(self.args)[0], 0)
            code, result, _ = self.call(self.args + ['--resume', '--apply'])
            self.assertEqual(code, 2)
            self.assertEqual(result['error']['code'], 'confirmation_required')
            self.assertTrue(all(c.args[1] == setup.PROBE for c in execute.call_args_list))

    def test_missing_dependencies_block_installation_and_identity_change_blocks_resume(self):
        self.identity['dependencies']['git-lfs'] = False
        with patch.object(setup, 'execute', return_value=self.identity) as execute:
            self.assertEqual(self.call(self.args)[0], 0)
            self.assertEqual(self.call(self.args + ['--resume', '--apply', '--yes'])[0], 2)
            self.assertTrue(all(c.args[1] == setup.PROBE for c in execute.call_args_list))
            self.identity['machine_id'] = 'another-machine'
            self.assertEqual(self.call(self.args + ['--resume'])[0], 2)

    def test_coolify_pins_image_and_loopback_without_global_configuration(self):
        unrelated = self.root / 'settings.json'
        unrelated.write_bytes(b'{"keep": "exact bytes"}\n')
        args = ['--target', 'coolify', '--image', 'PORT.registry.example/gitpickle@sha256:' + 'a' * 64,
                '--port', '32124', '--output', str(self.output), '--json']
        with patch.object(setup, 'execute', side_effect=AssertionError('Coolify must not execute')):
            self.assertEqual(self.call(args)[0], 0)
            files = {p.name: p.read_bytes() for p in self.output.iterdir()}
            self.assertIn(b'127.0.0.1:32124:8080', files['compose.yaml'])
            self.assertNotIn(b'SERVICE_FQDN', files['compose.yaml'])
            self.assertIn(b'image: PORT.registry.example/gitpickle@sha256:', files['compose.yaml'])
            self.assertEqual(self.call(args + ['--resume'])[0], 0)
            self.assertEqual(files, {p.name: p.read_bytes() for p in self.output.iterdir()})
            self.assertEqual(self.call(args + ['--resume', '--apply', '--yes'])[0], 2)
            self.assertEqual(unrelated.read_bytes(), b'{"keep": "exact bytes"}\n')
            bad = args.copy()
            bad[bad.index('--image') + 1] = 'registry.example/gitpickle:latest'
            self.assertEqual(self.call(bad)[0], 2)

    def test_real_probe_reads_tailscale_without_enrollment_or_configuration(self):
        bin_dir = self.root / 'probe-bin'
        bin_dir.mkdir()
        tailscale = bin_dir / 'tailscale'
        tailscale.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$HOME/tailscale-calls"
printf '%s\\n' '{"BackendState":"Running","Self":{"DNSName":"private.test.ts.net."}}'
''')
        tailscale.chmod(0o700)
        result = subprocess.run([sys.executable, '-I', '-c', setup.PROBE], capture_output=True,
            env=dict(os.environ, HOME=str(self.root), PATH=str(bin_dir)))
        self.assertEqual(result.returncode, 0, result.stderr)
        identity = json.loads(result.stdout)
        self.assertEqual(identity['tailscale'], dict(installed=True, state='Running', dns='private.test.ts.net.'))
        self.assertEqual((self.root / 'tailscale-calls').read_text().splitlines(), ['status --json'])
        tailscale.unlink()
        result = subprocess.run([sys.executable, '-I', '-c', setup.PROBE], capture_output=True,
            env=dict(os.environ, HOME=str(self.root), PATH=str(bin_dir)))
        self.assertEqual(json.loads(result.stdout)['tailscale']['state'], 'absent')
        self.assertEqual((self.root / 'tailscale-calls').read_text().splitlines(), ['status --json'])

    def test_interactive_protection_without_a_server_enters_setup(self):
        import gitpickle_workflow as workflow
        with patch.dict(os.environ, {}, clear=True), patch('sys.stdin.isatty', return_value=True), \
                patch.object(setup, 'main', return_value=0) as guide:
            self.assertEqual(workflow.main(['protect', 'github']), 0)
            guide.assert_called_once_with(['--configure'])

    def test_symlink_output_is_rejected(self):
        destination = self.root / 'unrelated'
        destination.mkdir()
        self.output.symlink_to(destination, target_is_directory=True)
        with patch.object(setup, 'execute', return_value=self.identity):
            self.assertEqual(self.call(self.args)[0], 2)
        self.assertEqual(list(destination.iterdir()), [])

    def test_ssh_pins_identity_and_does_not_inherit_unsafe_configuration(self):
        known = self.root / 'known_hosts'
        known.write_text('verified host key fixture')
        args = type('Args', (), dict(host='backup@host.example', known_hosts=known, ssh_port=2222, target='ssh'))()
        command = setup.ssh(args)
        for item in ['StrictHostKeyChecking=yes', 'BatchMode=yes', 'ForwardAgent=no', 'ControlPath=none',
                     'GlobalKnownHostsFile=/dev/null', 'ClearAllForwardings=yes', 'UpdateHostKeys=no']:
            self.assertIn(item, command)
        self.assertEqual(command[1:3], ['-F', '/dev/null'])
        self.assertEqual(command[-3:], ['-p', '2222', 'backup@host.example'])
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 255, b'', b'Host key verification failed')):
            with self.assertRaisesRegex(ValueError, 'pinned SSH identity'):
                setup.execute(args, setup.PROBE)
        args.host = '-oProxyCommand=bad'
        with self.assertRaises(ValueError):
            setup.ssh(args)

    def payload(self):
        with patch.object(setup, 'execute', return_value=self.identity):
            self.assertEqual(self.call(self.args)[0], 0)
        plan = json.loads((self.output / 'plan.json').read_text())
        return dict(identity=self.identity, sha256=plan['sha256'], unit=plan['unit'],
                    binary=setup.base64.b64encode(self.binary.read_bytes()).decode())

    def install(self, payload, fail=False):
        home = Path(self.identity['home'])
        home.mkdir(exist_ok=True)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir(exist_ok=True)
        systemctl = bin_dir / 'systemctl'
        systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/service-calls"\nexit ' + ('1' if fail else '0') + '\n')
        systemctl.chmod(0o700)
        return subprocess.run([sys.executable, '-I', '-c', setup.INSTALL],
            input=json.dumps(payload).encode(), capture_output=True,
            env=dict(os.environ, HOME=str(home), PATH=str(bin_dir) + os.pathsep + os.environ['PATH']))

    def test_actual_install_replays_after_reload_failure_preserving_runtime_files(self):
        payload = self.payload()
        home = Path(self.identity['home'])
        config = home / '.config/gitpickle'
        config.mkdir(parents=True, mode=0o700)
        for name in ('policy.json', 'runtime.env'):
            (config / name).write_bytes(b'private runtime content')
        self.assertNotEqual(self.install(payload, fail=True).returncode, 0)
        self.assertEqual(self.install(payload).returncode, 0)
        self.assertEqual(self.install(payload).returncode, 0)
        releases = list((home / '.local/share/gitpickle/releases').iterdir())
        self.assertEqual(len(releases), 1)
        self.assertEqual((releases[0] / 'gitpickle').read_bytes(), self.binary.read_bytes())
        self.assertEqual((releases[0] / 'gitpickle').stat().st_mode & 0o777, 0o700)
        for name in ('policy.json', 'runtime.env'):
            self.assertEqual((config / name).read_bytes(), b'private runtime content')
        self.assertEqual((home / 'service-calls').read_text().splitlines(), ['--user daemon-reload'] * 3)

    def test_install_refuses_conflicting_unit_checksum_and_symlink(self):
        payload = self.payload()
        home = Path(self.identity['home'])
        unit = home / '.config/systemd/user/gitpickle.service'
        unit.parent.mkdir(parents=True)
        unit.write_text('unrelated service')
        self.assertNotEqual(self.install(payload).returncode, 0)
        self.assertEqual(unit.read_text(), 'unrelated service')
        self.assertFalse((home / '.local').exists())
        unit.unlink()
        invalid = dict(payload, binary=setup.base64.b64encode(b'bad bytes').decode())
        self.assertNotEqual(self.install(invalid).returncode, 0)
        self.assertFalse((home / '.local').exists())
        unrelated = self.root / 'other'
        unrelated.mkdir()
        (home / '.local').symlink_to(unrelated, target_is_directory=True)
        self.assertNotEqual(self.install(payload).returncode, 0)
        self.assertEqual(list(unrelated.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
