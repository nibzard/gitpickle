"""Archive-only recovery acceptance tests."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import gitpickle_archive as a
import gitpickle_perf as perf


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'archive')+'-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'test-archive')

    def fixture(self, name='empty-and-tiny'):
        source = self.root / name
        metadata = perf.generate(name, source, profile='smoke')
        return source, metadata

    def test_empty_and_historical_recovery_without_source(self):
        source, _ = self.fixture()
        for repo in sorted(source.glob('*.git')):
            p = a.capture(self.store, repo, repo.stem, 'github:stable-id')
            expected = a.git(repo, 'show-ref', '--head') if repo.stem != 'empty' else b''
            head = a.git(repo, 'symbolic-ref', 'HEAD')
            shutil.rmtree(repo)
            destination = self.root / ('restored-'+repo.stem)
            a.restore(self.store, p, destination)
            self.assertEqual(head, a.git(destination, 'symbolic-ref', 'HEAD'))
            if expected:
                self.assertEqual(expected, a.git(destination, 'show-ref', '--head'))

    def test_complete_inventory_reuse_missing_pack_and_corruption(self):
        source, _ = self.fixture()
        repo = source / 'tiny.git'
        first = a.capture(self.store, repo, 'r', 'github:1')
        a.git(repo, 'update-ref', '-d', 'refs/heads/topic')
        second = a.capture(self.store, repo, 'r', 'github:1')
        self.assertNotEqual(first, second)
        p = a.read(self.store, second, 'point')
        self.assertEqual(a.read(self.store, first, 'point')['packs'], p['packs'])
        # Restore does not traverse earlier points or publications.
        shutil.rmtree(self.store / 'repos/r/commits')
        (self.store / first).unlink()
        shutil.rmtree(source)
        a.restore(self.store, second, self.root / 'ok')
        pack = self.store / p['packs'][0]['key']
        original = pack.read_bytes()
        pack.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            a.restore(self.store, second, self.root / 'bad')
        pack.unlink()
        with self.assertRaises(FileNotFoundError):
            a.restore(self.store, second, self.root / 'missing')
        pack.write_bytes(original)
        (self.store / second).write_bytes(b'{}')
        with self.assertRaises(ValueError):
            a.restore(self.store, second, self.root / 'bad-record')

    def test_force_push_retains_both_points(self):
        source, _ = self.fixture()
        repo = source / 'tiny.git'
        before = a.capture(self.store, repo, 'r', 'github:1')
        old_refs = a.git(repo, 'for-each-ref')
        tree = a.git(repo, 'mktree', data=b'').decode().strip()
        replacement = perf.git(repo, 'commit-tree', tree, '-m', 'replacement root').decode().strip()
        for ref in a.read(self.store, before, 'point')['refs']:
            a.git(repo, 'update-ref', '-d', ref)
        a.git(repo, 'update-ref', 'refs/heads/main', replacement)
        after = a.capture(self.store, repo, 'r', 'github:1')
        new_refs = a.git(repo, 'for-each-ref')
        shutil.rmtree(source)
        a.restore(self.store, before, self.root / 'before')
        a.restore(self.store, after, self.root / 'after')
        self.assertEqual(old_refs, a.git(self.root / 'before', 'for-each-ref'))
        self.assertEqual(new_refs, a.git(self.root / 'after', 'for-each-ref'))

    def test_versions_chains_and_unsafe_paths(self):
        source, _ = self.fixture()
        p = a.capture(self.store, source / 'tiny.git', 'r', 'github:1')
        unknown = a.encode(dict(a.read(self.store, p, 'point'), version=1)).replace(b'\"version\":1', b'\"version\":2')
        key = 'repos/r/points/'+a.digest(unknown)
        a.put(self.store, key, unknown)
        with self.assertRaises(ValueError):
            a.restore(self.store, key, self.root / 'unsupported-point')
        chain = a.inventory(self.store, 'r')
        c = a.read(self.store, chain[0], 'publication')
        a.record(self.store, 'repos/r/commits', dict(c, point=p, extra='fork'))
        with self.assertRaises(ValueError):
            a.inventory(self.store, 'r')
        for key in ('../outside', '/etc/passwd', 'repos//bad'):
            with self.assertRaises(ValueError):
                a.read(self.store, key)
        (self.store / 'archive.json').write_text(json.dumps({'kind': 'archive', 'version': 2}))
        with self.assertRaises(ValueError):
            a.restore(self.store, p, self.root / 'unknown')

    def test_lfs_and_incremental_packs(self):
        source, meta = self.fixture('lfs-2g')
        repo = next(source.glob('*.git'))
        incomplete = a.capture(self.store, repo, 'r', 'fixture')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            a.restore(self.store, incomplete, self.root / 'refuse-incomplete')
        partial = a.restore(self.store, incomplete, self.root / 'partial', allow_incomplete=True)
        self.assertFalse(partial['complete'])
        self.assertEqual(8, len(partial['missing_lfs']))
        self.assertEqual(['git'], partial['verified_components'])
        first = a.capture(self.store, repo, 'r', 'fixture', source / 'lfs')
        self.assertEqual(first, a.capture(self.store, repo, 'r', 'fixture', source / 'lfs'))
        parent = a.git(repo, 'rev-parse', 'HEAD').decode().strip()
        tree = a.git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip()
        commit = perf.git(repo, 'commit-tree', tree, '-p', parent, '-m', 'new').decode().strip()
        a.git(repo, 'update-ref', 'refs/heads/main', commit)
        second = a.capture(self.store, repo, 'r', 'fixture', source / 'lfs')
        self.assertEqual(2, len(a.read(self.store, second, 'point')['packs']))
        shutil.rmtree(source)
        result = a.restore(self.store, second, self.root / 'lfs-restore')
        self.assertEqual(8, result['required_lfs'])
        for obj in meta['lfs']:
            oid = obj['oid']
            data = (self.root / 'lfs-restore' / f'lfs/objects/{oid[:2]}/{oid[2:4]}/{oid}').read_bytes()
            self.assertEqual(oid, a.digest(data))

    def test_sha256_detached_head_and_missing_predecessor(self):
        repo = self.root / 'sha256'
        repo.mkdir()
        a.git(repo, 'init', '--bare', '--object-format=sha256')
        tree = a.git(repo, 'mktree', data=b'').decode().strip()
        blob = a.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit-tree', tree, data=b'test\n').decode().strip()
        a.git(repo, 'update-ref', '--no-deref', 'HEAD', blob)
        p = a.capture(self.store, repo, 'sha256', 'local:test')
        a.restore(self.store, p, self.root / 'sha256-restored')
        self.assertEqual(blob, a.git(self.root / 'sha256-restored', 'rev-parse', 'HEAD').decode().strip())
        chain = a.inventory(self.store, 'sha256')
        c = a.read(self.store, chain[0], 'publication')
        (self.store / chain[0]).unlink()
        a.record(self.store, 'repos/sha256/commits', dict(c, parent='repos/sha256/commits/'+'0'*64))
        with self.assertRaises(ValueError):
            a.inventory(self.store, 'sha256')


if __name__ == '__main__':
    unittest.main()
