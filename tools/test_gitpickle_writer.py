"""Single-writer acceptance tests, without provider credentials."""
import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import gitpickle_archive as a
from gitpickle_writer import Writer


class WriterTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'writer')+'-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'ownership-test')
        self.repo = self.root / 'empty'
        self.repo.mkdir()
        a.git(self.repo, 'init', '--bare')

    def test_competing_process_and_release_on_exit(self):
        script = 'from gitpickle_writer import Writer; import sys;\nwith Writer(sys.argv[1], "server-a"): pass'
        env = dict(os.environ, PYTHONPATH=str(Path('tools').resolve()))
        with Writer(self.store, 'server-a'):
            result = subprocess.run([sys.executable, '-c', script, str(self.store)], env=env, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn(b'BlockingIOError', result.stderr)
        result = subprocess.run([sys.executable, '-c', script, str(self.store)], env=env, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_process_death_releases_lock_without_removing_identity(self):
        script = ('from gitpickle_writer import Writer; import sys, time;\n'
                  'with Writer(sys.argv[1], "server-a"):\n'
                  ' print("locked", flush=True); time.sleep(30)')
        env = dict(os.environ, PYTHONPATH=str(Path('tools').resolve()))
        process = subprocess.Popen([sys.executable, '-c', script, str(self.store)],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(b'locked\n', process.stdout.readline())
            process.kill()
            process.wait(timeout=5)
            with Writer(self.store, 'server-a') as resumed:
                self.assertTrue(resumed.writable)
                self.assertEqual(1, len(resumed.ownership()))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_foreign_server_read_only_and_safe_takeover(self):
        with Writer(self.store, 'server-a') as first:
            point = first.capture(self.repo, 'r', 'local:test')
            owner = first.owner_key
        with Writer(self.store, 'server-b') as second:
            self.assertFalse(second.writable)
            a.restore(self.store, point, self.root / 'restored')
            with self.assertRaises(PermissionError):
                second.capture(self.repo, 'r', 'local:test')
            heads = second.reconcile()
            for evidence in ('', None):
                with self.assertRaises(PermissionError):
                    second.takeover(owner, evidence, heads)
            with self.assertRaises(ValueError):
                second.takeover(None, 'credentials revoked: ticket 1', heads)
            with self.assertRaises(ValueError):
                second.takeover(owner, 'credentials revoked: ticket 1', {})
            second.takeover(owner, 'credentials revoked: ticket 1', heads)
            self.assertTrue(second.writable)
            second.capture(self.repo, 'r', 'local:test')
        with Writer(self.store, 'server-a') as old:
            self.assertFalse(old.writable)
        with Writer(self.store, 'server-b') as resumed:
            self.assertTrue(resumed.writable)
            self.assertEqual(2, len(resumed.ownership()))

    def test_concurrent_jobs_serialize_and_other_repositories_progress(self):
        with Writer(self.store, 'server-a') as writer:
            barrier = threading.Barrier(8)
            def capture(_):
                barrier.wait(timeout=5)
                return writer.capture(self.repo, 'r', 'local:test')
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                points = list(pool.map(capture, range(8)))
            self.assertEqual(1, len(set(points)))
            self.assertEqual(1, len(a.inventory(self.store, 'r')))
            with writer.mutation('r'):
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    self.assertIsNotNone(pool.submit(writer.capture, self.repo, 'other', 'local:test').result(timeout=5))
                with self.assertRaises(PermissionError):
                    writer.takeover(writer.owner_key, 'revoked', writer.reconcile())

    def test_conflicts_stop_all_mutation_without_timestamp_selection(self):
        with Writer(self.store, 'server-a') as writer:
            point = writer.capture(self.repo, 'r', 'local:test')
            head = a.inventory(self.store, 'r')[0]
            original = a.read(self.store, head, 'publication')
            a.record(self.store, 'repos/r/commits', dict(original, recorded_at='9999-01-01T00:00:00Z'))
            before = set(self.store.rglob('*'))
            with self.assertRaises(ValueError):
                writer.capture(self.repo, 'other', 'local:test')
            self.assertEqual(before, set(self.store.rglob('*')))
            with self.assertRaises(PermissionError):
                writer.takeover(writer.owner_key, 'revoked', {})
            # Restore remains possible even though automatic selection stops.
            a.restore(self.store, point, self.root / 'recovered')
            (self.store / head).unlink()
            with self.assertRaises(PermissionError):
                writer.capture(self.repo, 'other', 'local:test')

    def test_ownership_fork_and_legacy_adoption(self):
        with Writer(self.store, 'server-a') as writer:
            owner = a.read(self.store, writer.owner_key, 'writer')
            a.record(self.store, 'ownership', dict(owner, server_id='intruder'))
            with self.assertRaises(ValueError):
                writer.capture(self.repo, 'r', 'local:test')
        with self.assertRaises(ValueError):
            with Writer(self.store, 'server-a'):
                pass
        legacy = self.root / 'legacy'
        a.initialize(legacy, 'legacy')
        a._capture(legacy, self.repo, 'r', 'local:test')
        with Writer(legacy, 'server-a') as writer:
            self.assertFalse(writer.writable)
            writer.takeover(None, 'legacy writer stopped and access revoked', writer.reconcile())
            self.assertTrue(writer.writable)


if __name__ == '__main__':
    unittest.main()
