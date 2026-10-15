"""Check resource recovery without deleting resources owned by other runs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gitpickle_dev_env import CLEANUP, ROOT, register_cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'tests/results/dev-env'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(
            prefix=os.getenv('LOOPER_RESOURCE_PREFIX', 'local') + '-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.prefix = os.getenv('LOOPER_RESOURCE_PREFIX', 'local')
        self.token = 'testtoken'
        self.path = self.base / f'gitpickle-{self.prefix}-{self.token}'
        self.marker = dict(prefix=self.prefix, token=self.token, artifact='test-artifact', uid=os.getuid())

    def create(self):
        self.path.mkdir()
        (self.path / '.gitpickle-owner.json').write_text(json.dumps(self.marker))
        (self.path / 'payload').write_text('test')

    def cleanup(self, path=None):
        subprocess.run([sys.executable, '-c', CLEANUP, str(path or self.path),
                        self.prefix, self.token, 'test-artifact', ''], check=True)

    def test_registration_precedes_creation_and_preserves_setup(self):
        registry = self.base / 'cleanup.sh'
        registry.write_text('# Existing cleanup\ntrue\n')
        args = register_cleanup(self.path, self.prefix, self.token, 'test-artifact', '', registry)
        self.assertFalse(self.path.exists())
        self.assertTrue(registry.read_text().startswith('# Existing cleanup\ntrue\n'))
        self.create()
        subprocess.run(args, check=True)
        self.assertFalse(self.path.exists())
        subprocess.run(['bash', str(registry)], check=True)

    def test_wrong_owner_marker_is_preserved(self):
        self.marker['prefix'] = 'someone-else'
        self.create()
        self.cleanup()
        self.assertTrue((self.path / 'payload').exists())

    def test_missing_marker_is_preserved(self):
        self.path.mkdir()
        self.cleanup()
        self.assertTrue(self.path.exists())

    def test_symlink_root_and_marker_are_preserved(self):
        target = self.base / 'unrelated'
        target.mkdir()
        marker = target / '.gitpickle-owner.json'
        marker.write_text(json.dumps(self.marker))
        self.path.symlink_to(target, target_is_directory=True)
        self.cleanup()
        self.assertTrue(target.exists())
        self.path.unlink()
        self.path.mkdir()
        (self.path / '.gitpickle-owner.json').symlink_to(marker)
        self.cleanup()
        self.assertTrue(self.path.exists())

    def test_owned_tree_does_not_follow_child_symlink(self):
        self.create()
        target = self.base / 'unrelated'
        target.mkdir()
        (target / 'keep').write_text('keep')
        (self.path / 'link').symlink_to(target, target_is_directory=True)
        self.cleanup()
        self.cleanup()
        self.assertTrue((target / 'keep').exists())

    def test_container_cleanup_requires_every_ownership_label(self):
        labels = {'io.looper.resource-prefix': self.prefix,
                  'io.looper.artifact': 'test-artifact', 'io.gitpickle.dev-token': self.token}
        for changed in [None, *labels]:
            actual = dict(labels)
            if changed:
                actual[changed] = 'unrelated'
            inspected = subprocess.CompletedProcess([], 0,
                json.dumps([{'Config': {'Labels': actual}}]), '')
            with patch.object(sys, 'argv', ['cleanup', str(self.path), self.prefix,
                                          self.token, 'test-artifact', 'owned-container']), \
                    patch('subprocess.run', return_value=inspected) as run:
                exec(CLEANUP, {})
            self.assertEqual(run.call_count, 1 if changed else 2)
            if not changed:
                self.assertEqual(run.call_args.args[0],
                                 ['docker', 'container', 'rm', '-f', 'owned-container'])

    def test_container_inspection_failure_is_not_success(self):
        failed = subprocess.CompletedProcess([], 1, '', 'Cannot connect to Docker daemon')
        with patch.object(sys, 'argv', ['cleanup', str(self.path), self.prefix,
                                      self.token, 'test-artifact', 'owned-container']), \
                patch('subprocess.run', return_value=failed), \
                self.assertRaises(RuntimeError):
            exec(CLEANUP, {})


if __name__ == '__main__':
    unittest.main()
