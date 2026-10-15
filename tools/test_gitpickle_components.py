"""Required LFS and wiki coverage, independent recovery, and submodule boundaries."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_security as security
import gitpickle_recovery as r
from gitpickle_github import GitHub
from gitpickle_writer import Writer


class ComponentTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated/components')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'components') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'components')
        self.repo = self.make_repo('source')
        self.wiki = self.make_repo('wiki')
        self.payload = b'required historical large file'
        self.oid = a.digest(self.payload)
        self.pointer = (f'version https://git-lfs.github.com/spec/v1\noid sha256:{self.oid}\n'
                        f'size {len(self.payload)}\n').encode()
        self.lfs = self.root / 'lfs'
        self.lfs.mkdir()

    def make_repo(self, name):
        repo = self.root / name
        repo.mkdir()
        a.git(repo, 'init', '--bare', '--template=', '--initial-branch=main')
        self.commit(repo, b'initial')
        return repo

    def commit(self, repo, blob, parent=None, gitlink=None, filename='file'):
        oid = a.git(repo, 'hash-object', '-w', '--stdin', data=blob).decode().strip()
        entries = f'100644 blob {oid}\t{filename}\n'
        if gitlink:
            entries += f'160000 commit {gitlink}\tdependency\n'
        tree = a.git(repo, 'mktree', data=entries.encode()).decode().strip()
        commit = a.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, *(['-p', parent] if parent else []),
                       data=b'component test\n').decode().strip()
        a.git(repo, 'update-ref', 'refs/heads/main', commit)
        return commit

    def capture(self, **kwargs):
        return a.capture(self.store, self.repo, 'r', 'github:1', **kwargs)

    def forbid_payload(self, root, key, payload):
        self.assertNotIn('/packs/', key)
        self.assertNotIn('/lfs/', key)
        self.put(root, key, payload)

    def test_missing_corrupt_then_available_historical_lfs_and_zero_uploads(self):
        old = self.commit(self.repo, self.pointer)
        self.commit(self.repo, b'pointer removed from current tree', parent=old)
        (self.lfs / self.oid).write_bytes(b'wrong digest')
        partial = self.capture(lfs=self.lfs)
        state = r.point_info(self.store, partial)
        self.assertFalse(state['complete'])
        self.assertEqual(1, len(state['missing']))
        self.assertFalse(r.storage_inventory(self.store)['repositories'][0]['points'][0]['verified'])
        with self.assertRaisesRegex(ValueError, 'no complete verified'):
            r.plan(self.store, 'r')
        self.put = a.put
        with patch.object(a, 'put', self.forbid_payload):
            self.assertEqual(partial, self.capture(lfs=self.lfs))
        (self.lfs / self.oid).write_bytes(self.payload)
        complete = self.capture(lfs=self.lfs)
        self.assertNotEqual(partial, complete)
        self.assertEqual(a.read(self.store, partial, 'point')['packs'],
                         a.read(self.store, complete, 'point')['packs'])
        self.assertEqual('incomplete', a.read(self.store, partial, 'point')['coverage']['lfs'])
        self.assertEqual(complete, r.plan(self.store, 'r')['point'])
        shutil.rmtree(self.lfs)
        with patch.object(a, 'put', self.forbid_payload):
            self.assertEqual(complete, self.capture())
        shutil.rmtree(self.repo)
        result = a.restore(self.store, complete, self.root / 'recovered')
        self.assertTrue(result['complete'])
        self.assertEqual(['git', 'lfs'], result['verified_components'])
        archived = self.store / f'repos/r/lfs/{self.oid}'
        archived.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'digest'):
            a.restore(self.store, complete, self.root / 'corrupt')
        self.assertFalse((self.root / 'corrupt').exists())

    def test_enabled_unavailable_wiki_then_independent_recovery(self):
        partial = self.capture(wiki=False)
        self.assertEqual({'git': 'complete', 'lfs': 'complete', 'wiki': 'incomplete'},
                         a.read(self.store, partial, 'point')['coverage'])
        result = a.restore(self.store, partial, self.root / 'partial', True)
        self.assertFalse(result['complete'])
        self.assertEqual(['git', 'lfs'], result['verified_components'])
        child = a.capture(self.store, self.wiki, 'r-wiki', 'github:1:wiki')
        complete = self.capture(wiki=child)
        self.put = a.put
        with patch.object(a, 'put', self.forbid_payload):
            self.assertEqual(child, a.capture(self.store, self.wiki, 'r-wiki', 'github:1:wiki'))
            self.assertEqual(complete, self.capture(wiki=child))
        # Only archive bytes survive. Neither child publication nor local caches are needed.
        shutil.rmtree(self.wiki)
        shutil.rmtree(self.repo)
        shutil.rmtree(self.store / '.projection')
        shutil.rmtree(self.store / 'repos/r-wiki/commits')
        result = a.restore(self.store, complete, self.root / 'restored')
        self.assertTrue(result['complete'])
        self.assertEqual(['git', 'lfs', 'wiki'], result['verified_components'])
        self.assertEqual('passed', result['wiki']['git_integrity'])
        a.git(self.root / 'restored/wiki.git', 'fsck', '--full', '--strict')
        pack = self.store / a.read(self.store, child, 'point')['packs'][0]['key']
        pack.write_bytes(b'bad wiki digest')
        with self.assertRaisesRegex(ValueError, 'digest'):
            a.restore(self.store, complete, self.root / 'bad-wiki')
        self.assertFalse((self.root / 'bad-wiki').exists())

    def test_wiki_lfs_has_independent_coverage_and_digest(self):
        self.commit(self.wiki, self.pointer)
        child = a.capture(self.store, self.wiki, 'r-wiki', 'github:1:wiki')
        partial = self.capture(wiki=child)
        self.assertEqual('incomplete', a.read(self.store, partial, 'point')['coverage']['wiki'])
        result = a.restore(self.store, partial, self.root / 'partial-wiki-lfs', True)
        self.assertEqual(['git', 'lfs'], result['verified_components'])
        self.assertEqual([self.oid], result['wiki']['missing_lfs'])
        (self.lfs / self.oid).write_bytes(self.payload)
        child = a.capture(self.store, self.wiki, 'r-wiki', 'github:1:wiki', self.lfs)
        complete = self.capture(wiki=child)
        standalone = a.restore(self.store, child, self.root / 'standalone-wiki')
        self.assertTrue(standalone['complete'])
        restored = a.restore(self.store, complete, self.root / 'full-wiki')
        self.assertTrue(restored['wiki']['complete'])
        lost = self.capture(wiki=False)
        self.assertFalse(r.point_info(self.store, lost)['complete'])
        self.assertEqual(complete, r.plan(self.store, 'r')['point'])
        archived = self.store / f'repos/r-wiki/lfs/{self.oid}'
        archived.write_bytes(b'corrupt wiki LFS')
        with self.assertRaisesRegex(ValueError, 'digest'):
            a.restore(self.store, complete, self.root / 'bad-wiki-lfs')
        self.assertFalse((self.root / 'bad-wiki-lfs').exists())

    def test_missing_upload_cannot_publish_complete_coverage(self):
        self.commit(self.repo, self.pointer)
        (self.lfs / self.oid).write_bytes(self.payload)
        original = a.durable_put
        def lose_lfs(root, key, payload):
            if '/lfs/' not in key:
                original(root, key, payload)
        with patch.object(a, 'durable_put', side_effect=lose_lfs):
            with self.assertRaisesRegex(ValueError, 'missing required component'):
                self.capture(lfs=self.lfs)
        self.assertEqual([], a.inventory(self.store, 'r'))

    def test_wiki_identity_cannot_reference_unrelated_or_nested_components(self):
        unrelated = a.capture(self.store, self.wiki, 'other', 'other')
        with self.assertRaisesRegex(ValueError, 'wiki identity'):
            self.capture(wiki=unrelated)
        self.assertEqual([], a.inventory(self.store, 'r'))

    def test_gitlinks_preserved_without_dependent_fetches(self):
        oid = 'a' * 40
        self.commit(self.repo, b'[submodule "dependency"]\n url = https://untrusted.invalid/private\n', gitlink=oid, filename='.gitmodules')
        original = security.run
        def no_dependencies(command, **kwargs):
            self.assertNotIn('submodule', command)
            self.assertFalse(any('untrusted.invalid' in str(arg) for arg in command))
            return original(command, **kwargs)
        with patch.object(security, 'run', side_effect=no_dependencies):
            point = self.capture()
            result = a.restore(self.store, point, self.root / 'gitlinks')
        self.assertTrue(result['complete'])
        self.assertIn(oid.encode(), a.git(self.root / 'gitlinks', 'ls-tree', 'HEAD', 'dependency'))

    def test_github_enabled_wiki_and_partial_lfs_transfer(self):
        self.commit(self.repo, self.pointer)
        github = GitHub(lambda: 'test-token')
        github.get = lambda identity: {'id': 1, 'alias': 'owner/repo', 'has_wiki': True}
        original = security.run
        available = {'wiki': False, 'lfs': False}
        calls = []
        def transport(command, **kwargs):
            if 'lfs' in command:
                calls.append('lfs')
                self.assertEqual('http.https://github.com/.extraHeader', kwargs['env']['GIT_CONFIG_KEY_0'])
                self.assertIn('lfs.url=https://github.com/owner/repo.git/info/lfs', command)
                if not available['lfs']:
                    raise subprocess.CalledProcessError(1, ['git', 'lfs'])
                storage = Path(next(x.split('=', 1)[1] for x in command if x.startswith('lfs.storage=')))
                path = storage / 'objects' / self.oid[:2] / self.oid[2:4] / self.oid
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.payload)
                return subprocess.CompletedProcess(command, 0)
            if 'https://github.com/owner/repo.wiki.git' in command:
                calls.append('wiki')
                if not available['wiki']:
                    raise subprocess.CalledProcessError(128, ['git', 'clone'])
                return original(['git', 'clone', '--mirror', str(self.wiki), command[-1]], capture_output=True, check=True)
            if 'https://github.com/owner/repo.git' in command:
                return original(['git', 'clone', '--mirror', str(self.repo), command[-1]], capture_output=True, check=True)
            return original(command, **kwargs)
        class Service:
            root = self.store
            def checkpoint(self, job_id):
                pass
        service = Service()
        (self.store / '.projection').mkdir()
        with Writer(self.store) as writer, patch.object(security, 'run', side_effect=transport), \
                patch.object(security, 'github_addresses', return_value=['1.1.1.1']):
            service.writer = writer
            job = {'id': 'first', 'request': {'arguments': {'github_id': 1},
                   'operation': 'backup', 'repository': 'r', 'source': 'github:1'}}
            partial = github.execute(service, job)
            self.assertEqual('incomplete', a.read(self.store, partial, 'point')['coverage']['lfs'])
            self.assertEqual('incomplete', a.read(self.store, partial, 'point')['coverage']['wiki'])
            available.update(wiki=True, lfs=True)
            job['id'] = 'second'
            complete = github.execute(service, job)
            self.assertTrue(r.point_info(self.store, complete)['complete'])
            self.put = a.put
            job['id'] = 'third'
            with patch.object(a, 'put', self.forbid_payload):
                self.assertEqual(complete, github.execute(service, job))
        self.assertEqual(3, calls.count('wiki'))
        self.assertEqual(3, calls.count('lfs'))


if __name__ == '__main__':
    unittest.main()
