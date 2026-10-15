"""Concrete publication crash and response-loss acceptance tests."""
import os
from pathlib import Path
import shutil
import errno
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gitpickle_archive as a


class Crash(BaseException):
    pass


class CaptureTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'capture')+'-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'source.git'
        self.repo.mkdir()
        a.git(self.repo, 'init', '--bare', '--template=')
        self.tree = a.git(self.repo, 'mktree', data=b'').decode().strip()
        self.commit = a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                            'commit-tree', self.tree, data=b'first\n').decode().strip()
        a.git(self.repo, 'update-ref', 'refs/heads/main', self.commit)
        a.git(self.repo, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        self.store = self.root / 'archive'
        a.initialize(self.store, 'archive-test')

    def capture(self):
        return a.capture(self.store, self.repo, 'r', 'github:123')

    def test_every_crash_boundary_restarts_without_forks(self):
        faults = ['before_intent', 'after_intent', 'before_capture', 'after_capture', 'before_identity', 'after_identity',
                  'before_payload', 'after_payload', 'before_manifest', 'after_manifest',
                  'before_publication', 'after_publication', 'before_observation',
                  'after_observation', 'before_projection', 'after_projection',
                  'before_readback', 'after_readback', 'after_evidence', 'before_reply']
        for fault in faults:
            with self.subTest(fault=fault):
                self.store = self.root / fault
                a.initialize(self.store, 'archive-test')
                def crash(name):
                    if name == fault:
                        raise Crash(name)
                with patch.object(a, 'boundary', crash), self.assertRaises(Crash):
                    self.capture()
                # Publication alone never implies readback evidence.
                if fault in ('after_publication', 'before_readback'):
                    self.assertFalse(list((self.store / 'repos/r/checks').glob('*')))
                point = self.capture()
                self.assertEqual(1, len(a.inventory(self.store, 'r')))
                shutil.rmtree(self.store / '.projection')
                self.assertEqual(point, a.rebuild_projection(self.store, 'r')['point'])
                a.restore(self.store, point, self.root / (fault+'-restore'))

    def test_lost_responses_for_every_immutable_write(self):
        original = a.put
        writes = []
        def lose(root, key, data):
            original(root, key, data)
            writes.append(key)
            raise TimeoutError('response lost after durable write')
        with patch.object(a, 'put', lose):
            point = self.capture()
        self.assertTrue(any('/packs/' in key for key in writes))
        self.assertTrue(any('/points/' in key for key in writes))
        self.assertTrue(any('/commits/' in key for key in writes))
        self.assertEqual(point, self.capture())
        self.assertEqual(1, len(a.inventory(self.store, 'r')))

    def test_crash_before_and_after_each_immutable_write(self):
        original = a.put
        writes = []
        def track(root, key, data):
            if root == self.store:
                writes.append(key)
            original(root, key, data)
        with patch.object(a, 'put', track):
            self.capture()
        for index in range(len(writes)):
            for when in ('before', 'after'):
                with self.subTest(write=writes[index], when=when):
                    self.store = self.root / f'write-{index}-{when}'
                    a.initialize(self.store, 'archive-test')
                    count = 0
                    def interrupt(root, key, data):
                        nonlocal count
                        selected = root == self.store and count == index
                        if root == self.store:
                            count += 1
                        if selected and when == 'before':
                            raise Crash(key)
                        original(root, key, data)
                        if selected and when == 'after':
                            raise Crash(key)
                    with patch.object(a, 'put', interrupt), self.assertRaises(Crash):
                        a.capture(self.store, self.repo, 'r', 'github:123', operation_id='write-job')
                    point = a.capture(self.store, self.repo, 'r', 'github:123', operation_id='write-job')
                    self.assertEqual(1, len(a.inventory(self.store, 'r')))
                    a.restore(self.store, point, self.root / f'write-restore-{index}-{when}')

    def test_no_change_uploads_no_payload_and_refreshes_observation(self):
        first = self.capture()
        writes = []
        original = a.put
        def track(root, key, data):
            writes.append(key)
            original(root, key, data)
        with patch.object(a, 'put', track):
            self.assertEqual(first, self.capture())
        self.assertFalse(any('/packs/' in k or '/lfs/' in k for k in writes))
        observations = self.observations()
        self.assertEqual(2, len(observations))
        self.assertTrue(all(o['record_type'] == 'source_observation' for o in observations))
        self.assertEqual(1, len(a.inventory(self.store, 'r')))

    def observations(self):
        values = [a.read(self.store, 'control/'+p.name, 'control')
                  for p in (self.store / 'control').glob('*')]
        return [v for v in values if v['record_type'] == 'source_observation']

    def test_replay_after_publication_without_source_or_projection(self):
        def crash(name):
            if name == 'after_publication':
                raise Crash(name)
        with patch.object(a, 'boundary', crash), self.assertRaises(Crash):
            a.capture(self.store, self.repo, 'r', 'github:123', operation_id='job-1')
        point = a.read(self.store, a.inventory(self.store, 'r')[0], 'publication')['point']
        shutil.rmtree(self.repo)
        self.assertEqual(point, a.capture(self.store, self.repo, 'r', 'github:123', operation_id='job-1'))
        self.assertEqual([], self.observations())
        self.assertEqual(point, a.rebuild_projection(self.store, 'r')['point'])
        self.assertEqual(1, len(a.inventory(self.store, 'r')))
        with self.assertRaisesRegex(ValueError, 'different source'):
            a.capture(self.store, self.repo, 'r', 'github:456', operation_id='job-1')

    def test_no_change_replay_preserves_original_source_observation(self):
        point = self.capture()
        self.assertEqual(point, a.capture(self.store, self.repo, 'r', 'github:123', operation_id='job-2'))
        observations = self.observations()
        shutil.rmtree(self.repo)
        self.assertEqual(point, a.capture(self.store, self.repo, 'r', 'github:123', operation_id='job-2'))
        self.assertEqual(observations, self.observations())

    def test_local_flush_failure_cannot_be_reconciled_as_success(self):
        original = os.fsync
        def fail_directory(fd):
            import stat
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, 'directory flush failed')
            original(fd)
        from gitpickle_writer import Writer
        with Writer(self.store) as writer:
            with patch.object(os, 'fsync', fail_directory), self.assertRaises(OSError):
                writer.capture(self.repo, 'r', 'github:123')
        self.assertEqual([], a.inventory(self.store, 'r'))

    def test_replay_flushes_publication_before_acknowledging(self):
        point = a.capture(self.store, self.repo, 'r', 'github:123', operation_id='flush-job')
        publication = a.inventory(self.store, 'r')[0]
        original = a.put
        writes = []
        def track(root, key, data):
            writes.append(key)
            if key == publication:
                raise OSError(errno.EIO, 'publication flush failed')
            original(root, key, data)
        with patch.object(a, 'put', track), self.assertRaises(OSError):
            a.capture(self.store, self.repo, 'r', 'github:123', operation_id='flush-job')
        self.assertIn(publication, writes)
        self.assertEqual(point, a.capture(self.store, self.repo, 'r', 'github:123', operation_id='flush-job'))

    def test_unknown_and_conflicting_write_outcomes_stop_publication(self):
        original = a.put
        for mode in ('missing', 'unreadable', 'conflicting'):
            with self.subTest(mode=mode):
                self.store = self.root / mode
                a.initialize(self.store, 'archive-test')
                def fail(root, key, data):
                    if '/packs/' in key:
                        if mode == 'conflicting':
                            original(root, key, b'other bytes')
                        raise TimeoutError('unknown outcome')
                    original(root, key, data)
                original_read = a.read
                def read(root, key, kind=None):
                    if mode == 'unreadable' and '/packs/' in key:
                        raise PermissionError('cannot reconcile')
                    return original_read(root, key, kind)
                with patch.object(a, 'put', fail), patch.object(a, 'read', read), self.assertRaises((OSError, ValueError)):
                    self.capture()
                self.assertEqual([], a.inventory(self.store, 'r'))

    def test_transport_url_and_detached_head_outside_refs(self):
        detached = a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                         'commit-tree', self.tree, data=b'detached\n').decode().strip()
        a.git(self.repo, 'update-ref', '--no-deref', 'HEAD', detached)
        point = a.capture(self.store, self.repo.as_uri(), 'r', 'github:123')
        self.assertEqual({'detached': detached}, a.read(self.store, point, 'point')['head'])
        a.restore(self.store, point, self.root / 'detached')
        self.assertEqual(detached, a.git(self.root / 'detached', 'rev-parse', 'HEAD').decode().strip())

    def test_unborn_head_with_other_refs_is_preserved(self):
        a.git(self.repo, 'symbolic-ref', 'HEAD', 'refs/heads/unborn')
        point = self.capture()
        self.assertEqual({'symbolic': 'refs/heads/unborn'}, a.read(self.store, point, 'point')['head'])

    def test_empty_sha256_repository_preserves_format_and_head(self):
        repo = self.root / 'empty-sha256'
        repo.mkdir()
        a.git(repo, 'init', '--bare', '--object-format=sha256', '--initial-branch=unborn')
        point = a.capture(self.store, repo, 'r', 'github:123')
        manifest = a.read(self.store, point, 'point')
        self.assertEqual('sha256', manifest['hash_algorithm'])
        self.assertEqual({'symbolic': 'refs/heads/unborn'}, manifest['head'])
        self.assertEqual([], manifest['packs'])
        a.restore(self.store, point, self.root / 'empty-restored')

    def test_rewritten_history_survives_publication_crashes(self):
        replacement = a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                            'commit-tree', self.tree, data=b'rewritten root\n').decode().strip()
        faults = ('before_payload', 'after_payload', 'before_manifest', 'after_manifest',
                  'before_publication', 'after_publication', 'before_projection', 'after_projection',
                  'before_readback', 'after_readback', 'after_evidence', 'before_reply')
        for fault in faults:
            with self.subTest(fault=fault):
                self.store = self.root / ('history-'+fault)
                a.initialize(self.store, 'archive-test')
                a.git(self.repo, 'update-ref', 'refs/heads/main', self.commit)
                a.git(self.repo, 'update-ref', 'refs/tags/deleted', self.commit)
                first = self.capture()
                a.git(self.repo, 'update-ref', 'refs/heads/main', replacement)
                a.git(self.repo, 'update-ref', '-d', 'refs/tags/deleted')
                def crash(name):
                    if name == fault:
                        raise Crash(name)
                with patch.object(a, 'boundary', crash), self.assertRaises(Crash):
                    self.capture()
                second = self.capture()
                self.assertEqual(2, len(a.inventory(self.store, 'r')))
                before, after = self.root / ('old-'+fault), self.root / ('new-'+fault)
                a.restore(self.store, first, before)
                a.restore(self.store, second, after)
                self.assertEqual(self.commit, a.git(before, 'rev-parse', 'refs/tags/deleted').decode().strip())
                self.assertEqual(replacement, a.git(after, 'rev-parse', 'HEAD').decode().strip())
                self.assertEqual(b'refs/heads/main\n', a.git(after, 'for-each-ref', '--format=%(refname)'))

    def test_real_process_exit_during_publication(self):
        script = ('import os, sys; import gitpickle_archive as a\n'
                  'def stop(name):\n'
                  ' if name == sys.argv[3]: os._exit(73)\n'
                  'link = os.link\n'
                  'def interrupt_link(src, dst):\n'
                  ' link(src, dst)\n'
                  ' if sys.argv[3] == "link-" + dst.parent.name: os._exit(73)\n'
                  'os.link = interrupt_link\n'
                  'a.boundary = stop\n'
                  'a.capture(sys.argv[1], sys.argv[2], "r", "github:123", operation_id="crash-job")\n')
        env = dict(os.environ, PYTHONPATH=str(Path('tools').resolve()))
        for fault in ('after_payload', 'after_manifest', 'after_publication', 'after_evidence',
                      'link-packs', 'link-points', 'link-commits'):
            with self.subTest(fault=fault):
                self.store = self.root / ('process-'+fault)
                a.initialize(self.store, 'archive-test')
                result = subprocess.run([sys.executable, '-B', '-c', script, str(self.store), str(self.repo), fault],
                                        env=env, capture_output=True, timeout=15)
                self.assertEqual(73, result.returncode, result.stderr)
                point = a.capture(self.store, self.repo, 'r', 'github:123', operation_id='crash-job')
                self.assertEqual(1, len(a.inventory(self.store, 'r')))
                a.restore(self.store, point, self.root / ('process-restore-'+fault))

    def test_staging_is_independent_of_source_after_fetch(self):
        def remove_source(name):
            if name == 'after_capture':
                shutil.rmtree(self.repo)
        with patch.object(a, 'boundary', remove_source):
            point = self.capture()
        a.restore(self.store, point, self.root / 'recovered')
        self.assertEqual(self.commit, a.git(self.root / 'recovered', 'rev-parse', 'HEAD').decode().strip())

    def test_missing_git_objects_cannot_publish(self):
        # Git can reconstruct the well-known empty tree. Use a missing blob.
        blob = a.git(self.repo, 'hash-object', '-w', '--stdin', data=b'required').decode().strip()
        tree = a.git(self.repo, 'mktree', data=f'100644 blob {blob}\tfile\n'.encode()).decode().strip()
        commit = a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, data=b'nonempty\n').decode().strip()
        a.git(self.repo, 'update-ref', 'refs/heads/main', commit)
        (self.repo / 'objects' / blob[:2] / blob[2:]).unlink()
        with self.assertRaises(Exception):
            self.capture()
        self.assertEqual([], a.inventory(self.store, 'r'))

    def test_model_incomplete_closure_regression(self):
        # ArchiveCommit unsafe trace: upload, manifest, publish without closure.
        original = a.durable_put
        def lose_payload(root, key, data):
            if '/packs/' not in key:
                original(root, key, data)
        with patch.object(a, 'durable_put', lose_payload), self.assertRaises(FileNotFoundError):
            self.capture()
        self.assertEqual([], a.inventory(self.store, 'r'))

    def test_initial_capture_with_single_object_above_stdout_limit(self):
        import random
        random_bytes = random.Random(52).randbytes
        blob_file = self.root / 'large-blob'
        with blob_file.open('wb') as stream:
            for _ in range(65):
                stream.write(random_bytes(1024 * 1024))
        with blob_file.open('rb') as stream:
            blob = a.git(self.repo, 'hash-object', '-w', '--stdin', data=stream).decode().strip()
        tree = a.git(self.repo, 'mktree', data=f'100644 blob {blob}\tlarge\n'.encode()).decode().strip()
        commit = a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, data=b'large initial capture\n').decode().strip()
        a.git(self.repo, 'update-ref', 'refs/heads/main', commit)
        point = self.capture()
        manifest = a.read(self.store, point, 'point')
        self.assertGreater(max(pack['size'] for pack in manifest['packs']), 64 * 1024 * 1024)
        shutil.rmtree(self.repo)
        restored = self.root / 'large-restored.git'
        a.restore(self.store, point, restored)
        self.assertEqual(blob.encode(), a.git(restored, 'rev-parse', 'main:large').strip())
        self.assertEqual(65 * 1024 * 1024, int(a.git(restored, 'cat-file', '-s', blob)))

    def test_lfs_crashes_lost_response_and_no_change(self):
        lfs = self.root / 'lfs'
        lfs.mkdir()
        payload = b'required content'
        oid = a.digest(payload)
        (lfs / oid).write_bytes(payload)
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode()
        blob = a.git(self.repo, 'hash-object', '-w', '--stdin', data=pointer).decode().strip()
        tree = a.git(self.repo, 'mktree', data=f'100644 blob {blob}\tlarge.bin\n'.encode()).decode().strip()
        commit = a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, data=b'LFS\n').decode().strip()
        a.git(self.repo, 'update-ref', 'refs/heads/main', commit)
        for fault in ('before_lfs', 'after_lfs'):
            with self.subTest(fault=fault):
                self.store = self.root / fault
                a.initialize(self.store, 'archive-test')
                def crash(name):
                    if name == fault:
                        raise Crash(name)
                with patch.object(a, 'boundary', crash), self.assertRaises(Crash):
                    a.capture(self.store, self.repo, 'r', 'github:123', lfs)
                self.assertEqual([], a.inventory(self.store, 'r'))
                original = a.put
                def lose(root, key, data):
                    original(root, key, data)
                    if root == self.store:
                        raise TimeoutError('lost LFS response')
                with patch.object(a, 'put', lose):
                    point = a.capture(self.store, self.repo, 'r', 'github:123', lfs)
                def prohibit_payload(root, key, data):
                    if root == self.store:
                        self.assertFalse('/lfs/' in key or '/packs/' in key)
                    original(root, key, data)
                with patch.object(a, 'put', prohibit_payload):
                    self.assertEqual(point, a.capture(self.store, self.repo, 'r', 'github:123', lfs))

    def test_corrupt_readback_does_not_record_verification(self):
        def corrupt(name):
            if name == 'before_readback':
                next((self.store / 'repos/r/packs').iterdir()).write_bytes(b'corrupt')
        with patch.object(a, 'boundary', corrupt), self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(1, len(a.inventory(self.store, 'r')))
        self.assertFalse(list((self.store / 'repos/r/checks').glob('*')))


if __name__ == '__main__':
    unittest.main()
