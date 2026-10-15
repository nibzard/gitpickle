"""Native HTTP comparison harness and acknowledgment boundary regressions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import gitpickle_engine_spike as spike
import gitpickle_perf as fixtures


class EngineTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated/engine-spike')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'engine') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_stock_http_uses_real_git_and_requires_credentials(self):
        source = self.root / 'source.git'
        fixtures.make_repo(source, 8, 3, 4096)
        with spike.stock(self.root / 'stock') as base:
            url = spike.create(base, 'r', 'sha1', self.root / 'stock')
            spike.git(source, 'push', '--mirror', url)
            self.assertNotEqual(0, spike.git(source, 'ls-remote', url, auth=False, ok=False).returncode)
            destination = self.root / 'clone'
            spike.git(self.root, 'clone', '--mirror', url, str(destination))
            self.assertEqual(fixtures.refs(source), fixtures.refs(destination))
            spike.git(destination, 'fsck', '--full', '--strict')

    def test_post_receive_failure_does_not_gate_native_acknowledgment(self):
        source = self.root / 'source.git'
        fixtures.make_repo(source, 9, 1, 1024)
        remote = self.root / 'remote.git'
        remote.mkdir()
        spike.git(remote, 'init', '--bare', '--template=')
        (remote / 'hooks').mkdir()
        hook = remote / 'hooks/post-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        # Do not disable hooks in this command: this tests native Git itself.
        result = subprocess.run(['git', '-C', str(source), 'push', str(remote), 'main'],
                                env=spike.environment(), capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(spike.git(source, 'rev-parse', 'main').stdout,
                         spike.git(remote, 'rev-parse', 'main').stdout)


if __name__ == '__main__':
    unittest.main()
