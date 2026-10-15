"""Workflow acceptance through shared services and the real HTTP client."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_daemon as daemon
from gitpickle_github import GitHub
from gitpickle_jobs import Jobs
from gitpickle_plans import Grant, Plans, PlanError, Policy, PolicyStore, Principal
import gitpickle_workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated/workflow')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'workflow') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        # Give the fixture its own Git boundary, without an origin remote.
        # Otherwise native Git can inherit the enclosing project's remote.
        a.git(self.root, 'init', '--template=', '--initial-branch=main')
        self.archive = self.root / 'archive'
        a.initialize(self.archive, 'workflow-test')
        self.rows = {'1': dict(id=1, alias='alice/one', has_wiki=False),
                     '2': dict(id=2, alias='alice/two', has_wiki=False)}
        class Source:
            def repositories(inner):
                return dict(self.rows)
            def execute(inner, jobs, job):
                self.started.set()
                self.release.wait(10)
                return jobs.observe(job, self.source) if job['request']['operation'] == 'sync' else jobs.writer.capture(
                    self.source, job['request']['repository'], 'github', operation_id=job['id'])
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.source = self.root / 'source'
        self.source.mkdir()
        a.git(self.source, 'init', '--template=', '--initial-branch=main')
        (self.source / 'committed').write_text('data')
        a.git(self.source, 'add', 'committed')
        a.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
        a.git(self.source, 'remote', 'add', 'origin', 'git@github.com:alice/one.git')
        (self.source / 'dirty').write_text('uncommitted')
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self.release.set)
        grants = tuple(Grant('owner', permission, frozenset({'github-1', 'github-2'}), 2**62)
                       for permission in ('protect', 'backup', 'sync', 'status', 'plan_restore', 'apply_plan'))
        self.policies = PolicyStore(Policy(1, grants, frozenset({'owner'}), str(self.root)))
        self.plans = self.stack.enter_context(Plans(self.archive, self.policies))
        self.jobs = self.stack.enter_context(Jobs(self.archive, writer=self.plans.writer, source_adapter=Source(), source_interval=0))
        self.workflow = w.Workflow(self.plans, self.jobs, Source(), 1024)
        self.principal = Principal('owner')
        self.token = 't' * 40
        self.listener = self.stack.enter_context(daemon.server(daemon.Management(self.plans,
            {self.token: {'actor': 'owner', 'agent': False}}, self.workflow)))
        thread = threading.Thread(target=self.listener.serve_forever, daemon=True)
        thread.start()
        self.stack.callback(thread.join)
        self.stack.callback(self.listener.shutdown)
        self.url = 'http://127.0.0.1:' + str(self.listener.server_address[1])
        self.client = w.Client(self.url, self.token)

    def preview(self, all=False):
        return self.client.call('protect_preview', dict(repositories=None if all else [1], all=all, include_future=False))

    def apply(self, preview):
        return self.client.call('protect_apply', dict(reference=preview['reference'], digest=preview['digest']))

    def command(self, args, cwd=None):
        output, diagnostics = io.StringIO(), io.StringIO()
        before = Path.cwd()
        try:
            if cwd:
                os.chdir(cwd)
            with patch.dict(os.environ, GITPICKLE_MANAGEMENT_TOKEN=self.token), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics):
                code = w.main([*args, '--server', self.url])
        finally:
            os.chdir(before)
        return code, output.getvalue(), diagnostics.getvalue()

    def test_hosting_mode_commands_require_explicit_current_head(self):
        from gitpickle_hosting import Hosting
        grants = tuple(Grant('owner', permission, frozenset({'github-1'}), 2**62)
                       for permission in ('git_read', 'git_write', 'hosting_manage'))
        self.policies.replace(Policy(2, grants, frozenset(), str(self.root)))
        self.listener.application.hosting = Hosting(self.plans, 'https://example.invalid/git')
        code, output, _ = self.command(['hosting', '--repository', 'github-1', '--action', 'create', '--json'])
        self.assertEqual(0, code, output)
        result = self.client.call('hosting_status', dict(repository='github-1'))
        self.assertEqual('hosted', result['mode'])
        code, _, _ = self.command(['hosting', '--repository', 'github-1', '--action', 'resume', '--json'])
        self.assertNotEqual(0, code)
        code, _, _ = self.command(['hosting', '--repository', 'github-1', '--action', 'resume',
                                   '--expected-head', result['head'], '--preview', '--json'])
        self.assertNotEqual(0, code)
        self.assertEqual('hosted', self.client.call('hosting_status', dict(repository='github-1'))['mode'])
        code, output, _ = self.command(['hosting', '--repository', 'github-1', '--action', 'resume',
                                       '--expected-head', result['head'], '--json'])
        self.assertEqual(0, code, output)
        self.assertEqual('protected_mirror', self.client.call('hosting_status', dict(repository='github-1'))['mode'])

    def test_resumption_probes_and_idempotent_initial_jobs(self):
        preview = self.preview(all=True)
        self.assertEqual('filesystem_readback', preview['plan']['checks']['storage'])
        self.assertFalse(preview['plan']['checks']['bucket_creation'])
        self.assertEqual('UTC', preview['plan']['time_zone'])
        self.assertEqual(preview, self.client.call('protect_resume', {'reference': preview['reference']}))
        result = self.apply(preview)
        self.assertEqual(2, len(result['jobs']))
        with patch.object(self.workflow.github, 'repositories', side_effect=AssertionError('repeated authorization')):
            self.assertEqual(result, self.apply(preview))
        self.assertEqual([], self.workflow.protection.tick(staging_bytes=1024))
        self.assertEqual(2, len(self.jobs.jobs))
        self.assertEqual([], list((self.archive / '.projection').glob('probe-*')))

    def test_all_inventory_change_is_stale_and_policy_change_is_stale(self):
        preview = self.preview(all=True)
        self.rows['3'] = dict(id=3, alias='alice/three', has_wiki=False)
        with self.assertRaisesRegex(PlanError, 'plan_stale'):
            self.apply(preview)
        self.rows.pop('3')
        one = self.preview()
        self.apply(one)
        two = self.preview(all=True)
        self.apply(two)
        # An older preview cannot replace a different active policy.
        stale = self.preview()
        self.workflow.protection.protect(self.workflow.protection.preview([2]),
            self.workflow.protection.preview([2])['confirmation'])
        with self.assertRaisesRegex(PlanError, 'plan_stale'):
            self.apply(stale)

    def test_scope_key_reuse_and_changed_working_directory(self):
        self.apply(self.preview(all=True))
        code, out, _ = self.command(['backup', '--json', '--key', 'request', '--yes'], self.root)
        self.assertEqual(2, code)
        self.assertEqual('explicit_scope_required', json.loads(out)['error']['code'])
        code, out, _ = self.command(['backup', '--json', '--key', 'request', '--yes'], self.source)
        self.assertEqual(0, code)
        first = json.loads(out)
        self.assertEqual(['github-1'], first['scope']['repository_ids'])
        code, out, _ = self.command(['backup', '--json', '--key', 'request', '--all', '--yes'])
        self.assertEqual(2, code)
        self.assertEqual('conflict', json.loads(out)['error']['code'])
        self.assertEqual('git@github.com:alice/one.git', a.git(self.source, 'remote', 'get-url', 'origin').decode().strip())
        self.assertEqual('uncommitted', (self.source / 'dirty').read_text())
        code, out, _ = self.command(['backup', '--repository', 'github-1', '--json', '--key', 'request', '--yes'])
        self.assertEqual(first['job_id'], json.loads(out)['job_id'])

    def test_noninteractive_never_prompts_and_json_is_one_envelope(self):
        with patch.object(sys.stdin, 'isatty', return_value=False), patch('builtins.input', side_effect=AssertionError('prompt')), \
                patch.object(w.getpass, 'getpass', side_effect=AssertionError('secret prompt')):
            code, out, err = self.command(['protect', 'github', '--all', '--json'])
            self.assertEqual(2, code)
            self.assertEqual('confirmation_required', json.loads(out)['error']['code'])
            self.assertIn('Plan (UTC)', err)
            self.assertNotIn(self.token, err + out)

    def test_wait_sync_and_backup_have_distinct_results(self):
        self.apply(self.preview())
        self.jobs.start()
        code, out, err = self.command(['backup', '--repository', 'github-1', '--key', 'wait', '--yes', '--wait', '--json'])
        self.assertEqual(0, code, out + err)
        job = json.loads(out)['data']['jobs'][0]
        self.assertEqual('completed', job['state'])
        points = list((self.archive / 'repos/github-1/commits').glob('*'))
        code, out, err = self.command(['sync', '--repository', 'github-1', '--key', 'sync', '--yes', '--wait', '--json'])
        self.assertEqual(0, code, out + err)
        self.assertEqual(points, list((self.archive / 'repos/github-1/commits').glob('*')))
        self.assertEqual(0, self.command(['status', '--repository', 'github-1', '--json'])[0])

    def test_detachment_keeps_server_worker_running(self):
        self.apply(self.preview())
        self.release.clear()
        self.jobs.start()
        self.assertTrue(self.started.wait(5))
        original = self.client.call
        def interrupted(operation, args):
            if operation == 'capture_get':
                raise KeyboardInterrupt
            return original(operation, args)
        with patch.object(w.Client, 'call', side_effect=interrupted):
            code, out, err = self.command(['backup', '--repository', 'github-1', '--key', 'detach', '--yes', '--wait', '--json'])
        self.assertEqual(0, code)
        self.assertIn('Server work continues', err)
        detached = json.loads(out)['data']['jobs'][0]['id']
        self.assertFalse(self.jobs.inspect(detached)['cancel_requested'])
        self.release.set()
        self.assertEqual('completed', self.jobs.follow(detached)['state'])

    def test_probes_and_permissions_fail_before_changes(self):
        with patch.object(w.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(1, 1, 0)):
            with self.assertRaisesRegex(PlanError, 'insufficient_staging_disk'):
                self.preview()
        self.assertIsNone(self.workflow.protection.state['policy'])
        with self.assertRaisesRegex(PlanError, 'forbidden'):
            self.workflow.call(Principal('owner', True), 'protect_preview', dict(repositories=[1], all=False, include_future=False))
        self.policies.replace(Policy(2, (), frozenset(), str(self.root)))
        with self.assertRaisesRegex(PlanError, 'forbidden'):
            self.preview()

    def test_capture_preview_rejects_scope_changes_and_revoked_execution(self):
        self.apply(self.preview(all=True))
        preview = self.client.call('capture_preview', dict(repository=None, all=True, operation='backup', key='scope'))
        self.workflow.protection.protect(self.workflow.protection.preview([1]),
            self.workflow.protection.preview([1])['confirmation'])
        with self.assertRaisesRegex(PlanError, 'plan_stale'):
            self.client.call('capture_accept', dict(reference=preview['reference'], digest=preview['digest']))
        self.policies.replace(Policy(2, (), frozenset(), str(self.root)))
        self.jobs.start()
        for job in list(self.jobs.jobs):
            self.assertEqual('failed', self.jobs.follow(job)['state'])
            self.assertEqual('forbidden', self.jobs.inspect(job)['error'])

    def test_masked_credentials_narrow_output_and_color_free(self):
        self.apply(self.preview())
        with patch.dict(os.environ, NO_COLOR='1'), patch.object(w.shutil, 'get_terminal_size', return_value=os.terminal_size((30, 20))):
            text = w.display({'scope': 'github-1', 'result': 'accepted ' * 20})
            self.assertTrue(all(len(line) <= 30 for line in text.splitlines()))
            self.assertNotIn('\x1b', text)
        with patch.dict(os.environ, {'GITPICKLE_MANAGEMENT_TOKEN': ''}), patch.object(sys.stdin, 'isatty', return_value=True), \
                patch.object(w.getpass, 'getpass', return_value=self.token) as prompt, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, w.main(['status', '--server', self.url, '--all']))
            prompt.assert_called_once()

    def test_restore_uses_shared_approved_plan(self):
        self.apply(self.preview())
        self.jobs.start()
        for job in list(self.jobs.jobs):
            self.assertEqual('completed', self.jobs.follow(job)['state'])
        destination = self.root / 'recovered'
        code, out, err = self.command(['restore', 'github-1', '--destination', str(destination), '--yes', '--wait', '--json'])
        self.assertEqual(0, code, out + err)
        self.assertTrue((destination / 'HEAD').exists())
        self.assertEqual(1, json.loads(out)['schema_version'])
        self.assertEqual(2, self.command(['restore', 'github-1', '--destination', str(destination), '--yes', '--json'])[0])

    def test_setup_returns_replayable_nonsecret_recovery_card(self):
        preview = self.preview()
        result = self.apply(preview)
        card = result['recovery_card']
        self.assertEqual('workflow-test', card['archive_id'])
        self.assertEqual(1, card['format_version'])
        self.assertEqual('filesystem', card['storage']['provider'])
        self.assertEqual(card, self.apply(preview)['recovery_card'])
        self.assertNotIn(self.token, json.dumps(card))
        self.assertEqual([], card['required_application_keys'])

    def test_wire_negotiation_blocks_commands_before_dispatch(self):
        original = daemon.hello(True)
        cases = [({'management': {'minimum': 2, 'maximum': 3}}, 'unsupported_version'),
                 ({'machine_result': {'minimum': 2, 'maximum': 2}}, 'unsupported_version'),
                 ({'management': {'minimum': True, 'maximum': 1}}, 'invalid_input'),
                 ({'capabilities': []}, 'unsupported_capability')]
        for changes, code in cases:
            with self.subTest(changes=changes), patch.object(daemon, 'hello', return_value=dict(original, **changes)):
                with patch.object(self.workflow, 'call') as dispatch:
                    with self.assertRaises(PlanError) as raised:
                        self.client.call('protect_apply', {})
                    self.assertEqual(code, raised.exception.code)
                    dispatch.assert_not_called()
        future = dict(original, software_version='99.0', management={'minimum': 1, 'maximum': 4})
        with patch.object(daemon, 'hello', return_value=future):
            self.assertEqual(self.rows, self.client.call('workflow_inventory', {}))
        # Direct clients cannot skip schema selection or use an unsupported schema.
        for headers in ({}, {'X-GitPickle-Management-Version': '2', 'X-GitPickle-Result-Version': '1'}):
            import http.client
            connection = http.client.HTTPConnection('127.0.0.1', self.listener.server_address[1])
            try:
                with patch.object(self.workflow, 'call') as dispatch:
                    connection.request('POST', '/v1/management', json.dumps(dict(operation='protect_apply', arguments={})),
                        dict(headers, Authorization='Bearer ' + self.token))
                    response = connection.getresponse()
                    self.assertEqual('unsupported_version', json.loads(response.read())['error'])
                    dispatch.assert_not_called()
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
