"""Exercise installed binaries across restart, replacement, and index loss."""
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
import urllib.error
import urllib.request

import gitpickle_archive as archive
import gitpickle_daemon as daemon
from gitpickle_writer import Writer
from gitpickle_deployment_probe import PROCESS_CLEANUP


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('GITPICKLE_TEST_BINARY')
        if not configured:
            subprocess.run(['cargo', 'build', '--locked', '-p', 'gitpickle'], check=True)
        default = Path(os.environ.get('CARGO_TARGET_DIR') or 'target') / 'debug' / 'gitpickle'
        cls.binary = Path(configured or default).resolve()

    def setUp(self):
        base = Path('tests/results/deployment')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'deployment') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.state = self.root / 'archive'
        self.source = self.root / 'source'
        self.source.mkdir()
        archive.git(self.source, 'init', '--template=', '--initial-branch=main')
        (self.source / 'data').write_text('committed archive data')
        archive.git(self.source, 'add', 'data')
        archive.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-m', 'fixture')
        archive.initialize(self.state, 'deployment-fixture')
        with Writer(self.state) as writer:
            self.point = writer.capture(self.source, 'r', 'fixture')
        self.policy = self.root / 'policy.json'
        self.policy.write_text(json.dumps(dict(revision=1, grants=[], approvers=[], sandbox=str(self.root))))
        self.policy.chmod(0o600)
        self.token = 'test-only-token-' + 'x' * 40
        self.env = dict(os.environ, GITPICKLE_MANAGEMENT_TOKENS=json.dumps({self.token: dict(actor='owner', agent=False)}))
        for key in list(self.env):
            if key.startswith('GITPICKLE_STORAGE_'):
                self.env.pop(key)

    def start(self, binary, bootstrap=None):
        command = [str(binary), 'serve', '--state', str(self.state), '--policy', str(self.policy)]
        if bootstrap:
            command = [sys.executable, '-B', '-c', bootstrap, *command[2:]]
        process = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        self.addCleanup(self.stop_if_running, process)
        # A stalled startup must fail instead of hanging the test suite.
        lines = []
        reader = threading.Thread(target=lambda: lines.append(process.stdout.readline()), daemon=True)
        reader.start()
        reader.join(20)
        self.assertTrue(lines and lines[0], 'daemon did not become ready')
        endpoint = json.loads(lines[0])['listen']
        self.url = f'http://{endpoint[0]}:{endpoint[1]}'
        for route in ('/livez', '/readyz'):
            with urllib.request.urlopen(self.url + route, timeout=3) as response:
                self.assertEqual({'status': 'ready'}, json.load(response))
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(urllib.request.Request(self.url + '/v1/management', data=b'{}'), timeout=3)
        self.assertEqual(401, failure.exception.code)
        return process

    @staticmethod
    def stop_if_running(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    def stop(self, process):
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=10)
        self.assertEqual(0, process.returncode, err)
        self.assertIn('stopped', err)
        self.assertNotIn(self.token, out + err)

    def test_install_restart_release_replacement_and_projection_loss(self):
        old, new = self.root / 'releases/old', self.root / 'releases/new'
        old.mkdir(parents=True)
        new.mkdir()
        for release in (old, new):
            shutil.copy2(self.binary, release / 'gitpickle')
        current = self.root / 'current'
        current.symlink_to(old, target_is_directory=True)
        self.assertTrue(subprocess.check_output([str(current / 'gitpickle'), '--version']).startswith(b'gitpickle '))
        self.stop(self.start(current / 'gitpickle'))
        self.stop(self.start(current / 'gitpickle'))
        current.unlink()
        current.symlink_to(new, target_is_directory=True)
        self.stop(self.start(current / 'gitpickle'))
        shutil.rmtree(self.state / '.projection', ignore_errors=True)
        self.stop(self.start(current / 'gitpickle'))
        restored = self.root / 'restored'
        result = archive.restore(self.state, self.point, restored)
        self.assertTrue(result['complete'])
        self.assertEqual(b'committed archive data', archive.git(restored, 'show', 'refs/heads/main:data'))

    def test_wildcard_listener_requires_explicit_container_option(self):
        with self.assertRaises(ValueError):
            daemon.server(None, '0.0.0.0')
        with daemon.server(None, '0.0.0.0', container_network=True) as listener:
            self.assertGreater(listener.server_address[1], 0)

    def test_retained_process_cleanup_checks_ownership_and_is_idempotent(self):
        process = self.start(self.binary)
        cleanup = self.root / 'cleanup.py'
        cleanup.write_text(PROCESS_CLEANUP)
        for state in (self.root / 'another-state', self.state, self.state):
            subprocess.run([sys.executable, '-I', str(cleanup), str(self.binary), str(state)],
                           check=True, timeout=35)
            if state != self.state:
                self.assertIsNone(process.poll(), 'Cleanup stopped another deployment')
            else:
                self.assertEqual(0, process.wait(timeout=5))

    def test_sigterm_finishes_an_active_request_before_exit(self):
        marker = self.root / 'active-request'
        bootstrap = f'''import sys, time
from pathlib import Path
sys.path.insert(0, {str(Path('tools').resolve())!r})
import gitpickle_daemon as daemon
def call(self, principal, operation, arguments):
    Path({str(marker)!r}).touch()
    time.sleep(1)
    return {{'completed': True}}
daemon.Management.call = call
sys.exit(daemon.main())
'''
        process = self.start(self.binary, bootstrap)
        responses = []
        def request():
            message = urllib.request.Request(self.url + '/v1/management',
                data=json.dumps(dict(operation='slow_fixture', arguments={})).encode(),
                headers={'Authorization': 'Bearer ' + self.token,
                         'X-GitPickle-Management-Version': '1', 'X-GitPickle-Result-Version': '1'})
            with urllib.request.urlopen(message, timeout=5) as response:
                responses.append(json.load(response))
        client = threading.Thread(target=request)
        client.start()
        self.addCleanup(client.join, 5)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        self.stop(process)
        client.join(5)
        self.assertEqual([{'result': {'completed': True}}], responses)


if __name__ == '__main__':
    unittest.main()
