"""Storage-only recovery, selection, exports, and corruption acceptance tests."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_security as security
import gitpickle_recovery as r


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'recovery')+'-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'recovery-test')
        self.repo = self.root / 'source.git'
        self.repo.mkdir()
        a.git(self.repo, 'init', '--bare', '--template=', '--initial-branch=main')
        self.tree = a.git(self.repo, 'mktree', data=b'').decode().strip()
        self.first_commit = self.commit('original')
        a.git(self.repo, 'update-ref', 'refs/heads/main', self.first_commit)
        a.git(self.repo, 'update-ref', 'refs/tags/deleted', self.first_commit)
        self.first = a.capture(self.store, self.repo, 'r', 'https://github.com/lost/original')

    def commit(self, message, parent=None):
        return a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                     'commit-tree', self.tree, *(['-p', parent] if parent else []), data=message.encode()).decode().strip()

    def select(self, point=None, **kwargs):
        return r.plan(self.store, point=point, destination=self.root / 'restored', **kwargs)

    def manifest(self, **changes):
        return a.record(self.store, 'repos/r/points', dict(a.read(self.store, self.first, 'point'), **changes))

    def lose_server(self):
        shutil.rmtree(self.repo)
        shutil.rmtree(self.store / '.projection')
        (self.store / '.writer.lock').unlink()
        # The recovery reader must not need writable archive access.
        before = {str(p.relative_to(self.store)): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        return before

    def test_reconstruct_and_recover_history_with_dns_and_github_blocked(self):
        replacement = self.commit('rewritten')
        a.git(self.repo, 'update-ref', 'refs/heads/main', replacement)
        a.git(self.repo, 'update-ref', '-d', 'refs/tags/deleted')
        second = a.capture(self.store, self.repo, 'r', 'https://github.com/lost/original')
        # Publication survives a crash without verification evidence.
        a.git(self.repo, 'update-ref', 'refs/heads/main', self.commit('unverified'))
        def crash(name):
            if name == 'after_publication':
                raise RuntimeError('power lost')
        with patch.object(a, 'boundary', crash), self.assertRaises(RuntimeError):
            a.capture(self.store, self.repo, 'r', 'https://github.com/lost/original')
        before = self.lose_server()
        commands = []
        original_run = security.run
        def local_only(command, **kwargs):
            commands.append(command)
            self.assertFalse({'clone', 'fetch', 'ls-remote', 'remote', 'submodule'} & set(command))
            self.assertEqual('', kwargs['env']['GIT_ALLOW_PROTOCOL'])
            self.assertNotIn('GIT_ALTERNATE_OBJECT_DIRECTORIES', kwargs['env'])
            return original_run(command, **kwargs)
        with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS blocked')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('GitHub blocked')), \
             patch.object(security, 'run', local_only), \
             patch.dict(os.environ, {'GIT_ALTERNATE_OBJECT_DIRECTORIES': '/lost/cache', 'GIT_DIR': '/lost/database'}):
            inventory = r.storage_inventory(self.store)
            self.assertEqual([True, True, False], [p['verified'] for p in inventory['repositories'][0]['points']])
            plan = self.select()
            self.assertEqual(second, plan['point'])
            self.assertFalse(Path(plan['destination']).exists())
            result = r.execute(self.store, plan)
            self.assertTrue(result['complete'])
            old_plan = r.plan(self.store, point=self.first, destination=self.root / 'historical')
            r.execute(self.store, old_plan)
        self.assertTrue(commands)
        self.assertEqual(replacement, a.git(self.root / 'restored', 'rev-parse', 'HEAD').decode().strip())
        self.assertEqual(self.first_commit, a.git(self.root / 'historical', 'rev-parse', 'refs/tags/deleted').decode().strip())
        self.assertEqual(before, {str(p.relative_to(self.store)): p.read_bytes() for p in self.store.rglob('*') if p.is_file()})
        self.assertEqual([], list(self.root.glob('*-restore-*')))

    def test_incomplete_requires_explicit_point_and_warning(self):
        point = self.manifest(coverage={'git': 'complete', 'lfs': 'incomplete', 'wiki': 'missing'})
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.select(point)
        with self.assertRaisesRegex(ValueError, 'requires --point'):
            self.select(allow_incomplete=True)
        plan = self.select(point, allow_incomplete=True)
        self.assertTrue(any('Incomplete recovery' in w for w in plan['warnings']))
        result = r.execute(self.store, plan, allow_incomplete=True)
        self.assertFalse(result['complete'])
        self.assertEqual('passed', result['git_integrity'])

    def test_default_skips_incomplete_even_with_evidence(self):
        publication = a.inventory(self.store, 'r')[-1]
        point = self.manifest(coverage={'git': 'complete', 'lfs': 'incomplete'}, parent_publication=publication)
        a.record(self.store, 'repos/r/commits', {'kind': 'publication', 'archive_id': 'recovery-test',
                 'repository_id': 'r', 'point': point, 'parent': publication})
        a.record(self.store, 'repos/r/checks', {'kind': 'evidence', 'archive_id': 'recovery-test',
                 'repository_id': 'r', 'point': point, 'recorded_at': a.timestamp(), 'passed': True,
                 'method': 'archive_readback', 'reader_version': 1, 'components': ['git', 'lfs']})
        self.assertEqual(self.first, self.select()['point'])

    def test_no_verification_does_not_default_to_upload_success(self):
        shutil.rmtree(self.store / 'repos/r/checks')
        with self.assertRaisesRegex(ValueError, 'no complete verified'):
            self.select()
        r.execute(self.store, self.select(self.first))

    def test_full_bundle_is_usable_with_no_prerequisites(self):
        self.lose_server()
        result = r.execute(self.store, self.select(bundle=True))
        self.assertEqual('full', result['bundle_kind'])
        export = self.root / 'restored'
        metadata = json.loads((export / 'export.json').read_text())
        self.assertEqual([], metadata['prerequisites'])
        self.assertFalse(metadata['lfs_included'])
        consumer = self.root / 'consumer.git'
        consumer.mkdir()
        a.git(consumer, 'init', '--bare', '--template=')
        bundle = export / 'repository.bundle'
        a.git(consumer, 'bundle', 'verify', str(bundle))
        # Only this test permits local bundle transport; recovery itself disables transports.
        a.git(consumer, '-c', 'protocol.file.allow=always', 'bundle', 'unbundle', str(bundle))
        a.git(consumer, 'cat-file', '-e', self.first_commit+'^{commit}')
        heads = a.git(consumer, 'bundle', 'list-heads', str(bundle))
        self.assertIn(b'refs/tags/deleted', heads)
        self.assertIn(b' HEAD\n', heads)

    def test_corruption_missing_dependencies_and_invalid_refs_leave_no_destination(self):
        p = a.read(self.store, self.first, 'point')
        pack = self.store / p['packs'][0]['key']
        original = pack.read_bytes()
        for payload in (b'corrupt', None):
            if payload is None:
                pack.unlink()
            else:
                pack.write_bytes(payload)
            with self.assertRaises((ValueError, FileNotFoundError)):
                r.execute(self.store, self.select(self.first, allow_incomplete=True), allow_incomplete=True)
            self.assertFalse((self.root / 'restored').exists())
            pack.write_bytes(original)
        invalid = [self.manifest(refs={'refs/heads/main': '0'*40}),
                   self.manifest(head={'detached': '0'*40}),
                   self.manifest(refs={'refs/heads/../bad': self.first_commit}),
                   self.manifest(objects=p['objects']+['0'*40]),
                   self.manifest(packs=[dict(p['packs'][0], size=1)])]
        for point in invalid:
            with self.subTest(point=point), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                r.execute(self.store, self.select(point))
            self.assertFalse((self.root / 'restored').exists())
        self.assertEqual([], list(self.root.glob('*-restore-*')))

    def test_invalid_pack_with_matching_archive_digest_fails_git_integrity(self):
        payload = b'not a Git pack'
        key = 'repos/r/packs/'+a.digest(payload)
        a.put(self.store, key, payload)
        point = self.manifest(packs=[{'key': key, 'size': len(payload)}])
        with self.assertRaises(subprocess.CalledProcessError):
            r.execute(self.store, self.select(point))
        self.assertFalse((self.root / 'restored').exists())

    def test_lfs_missing_can_be_explicitly_recovered_but_corruption_cannot(self):
        payload = b'required LFS'
        oid = a.digest(payload)
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode()
        blob = a.git(self.repo, 'hash-object', '-w', '--stdin', data=pointer).decode().strip()
        self.tree = a.git(self.repo, 'mktree', data=f'100644 blob {blob}\tlarge\n'.encode()).decode().strip()
        a.git(self.repo, 'update-ref', 'refs/heads/main', self.commit('LFS'))
        lfs = self.root / 'lfs'
        lfs.mkdir()
        (lfs / oid).write_bytes(payload)
        point = a.capture(self.store, self.repo, 'r', 'https://github.com/lost/original', lfs)
        manifest = a.read(self.store, point, 'point')
        archived_lfs = self.store / manifest['lfs'][oid]['key']
        archived_lfs.unlink()
        self.assertEqual(self.first, self.select()['point'])
        plan = self.select(point, allow_incomplete=True)
        result = r.execute(self.store, plan, allow_incomplete=True)
        self.assertFalse(result['complete'])
        self.assertEqual([oid], result['missing_lfs'])
        shutil.rmtree(self.root / 'restored')
        archived_lfs.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.select(point, allow_incomplete=True)
        archived_lfs.write_bytes(payload)
        omitted = a.record(self.store, 'repos/r/points', dict(manifest, lfs={}))
        with self.assertRaisesRegex(ValueError, 'omits required LFS'):
            r.execute(self.store, self.select(omitted))

    def test_preview_default_directory_overwrite_empty_and_broken_chain(self):
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        plan = r.plan(self.store)
        self.assertFalse(Path(plan['destination']).exists())
        Path(plan['destination']).mkdir()
        self.assertNotEqual(plan['destination'], r.plan(self.store)['destination'])
        with self.assertRaisesRegex(ValueError, 'destination exists'):
            r.plan(self.store, destination=plan['destination'])
        publication = a.read(self.store, a.inventory(self.store, 'r')[0], 'publication')
        a.record(self.store, 'repos/r/commits', dict(publication, extra='fork'))
        with self.assertRaisesRegex(ValueError, 'fork'):
            r.plan(self.store)
        r.execute(self.store, r.plan(self.store, point=self.first, destination=self.root / 'explicit'))
        empty = self.manifest(refs={}, packs=[], objects=[], head={'symbolic': 'refs/heads/unborn'})
        empty_plan = r.plan(self.store, point=empty, destination=self.root / 'empty', bundle=True)
        with self.assertRaisesRegex(ValueError, 'empty repository'):
            r.execute(self.store, empty_plan)
        self.assertFalse((self.root / 'empty').exists())
        empty_plan['export'] = 'bare_repository'
        r.execute(self.store, empty_plan)
        self.assertEqual(b'refs/heads/unborn\n', a.git(self.root / 'empty', 'symbolic-ref', 'HEAD'))

    def test_evidence_identity_component_scope_and_digest(self):
        checks = self.store / 'repos/r/checks'
        shutil.rmtree(checks)
        evidence = {'kind': 'evidence', 'archive_id': 'recovery-test', 'repository_id': 'r',
                    'point': self.first, 'method': 'lfs_digest', 'components': ['git', 'lfs'],
                    'passed': True, 'reader_version': 1}
        key = a.record(self.store, 'repos/r/checks', evidence)
        with self.assertRaisesRegex(ValueError, 'no complete verified'):
            self.select()
        (self.store / key).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'digest'):
            r.storage_inventory(self.store)
        (self.store / key).unlink()
        a.record(self.store, 'repos/r/checks', dict(evidence, archive_id='other'))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            r.storage_inventory(self.store)

    def test_cli_preview_is_machine_readable_and_does_not_write(self):
        self.lose_server()
        command = [sys.executable, '-B', str(Path(__file__).with_name('gitpickle_recovery.py').resolve()),
                   '--from-storage', str(self.store), '--dry-run']
        output = subprocess.run(command, cwd=self.root, capture_output=True, check=True)
        plan = json.loads(output.stdout)
        self.assertEqual('not_run', plan['git_integrity'])
        self.assertFalse(Path(plan['destination']).exists())

    def test_recovery_card_and_symlink_checks(self):
        command = [sys.executable, '-B', str(Path(__file__).with_name('gitpickle_recovery.py').resolve()),
                   '--from-storage', str(self.store), '--dry-run', '--archive-id', 'recovery-test',
                   '--root-digest', a.digest(a.read(self.store, 'archive.json'))]
        result = subprocess.run(command, cwd=self.root, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        command[-1] = '0'*64
        result = subprocess.run(command, cwd=self.root, capture_output=True)
        self.assertEqual(1, result.returncode)
        self.assertIn(b'root digest', result.stderr)
        p = a.read(self.store, self.first, 'point')
        pack = self.store / p['packs'][0]['key']
        copied = self.root / 'outside.pack'
        copied.write_bytes(pack.read_bytes())
        pack.unlink()
        pack.symlink_to(copied)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.select()
        self.assertFalse((self.root / 'restored').exists())

    def test_multiple_repositories_require_selection(self):
        a.capture(self.store, self.repo, 'other', 'github:another-stable-id')
        with self.assertRaisesRegex(ValueError, 'select --repository'):
            self.select()
        plan = self.select(repository_id='other')
        self.assertEqual('other', plan['repository_id'])
        r.execute(self.store, plan)
        self.assertEqual(self.first_commit, a.git(self.root / 'restored', 'rev-parse', 'HEAD').decode().strip())


class RetainedReaderTests(unittest.TestCase):
    def test_retained_v1_archive_recovers_without_writer_state(self):
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-compat-') as tmp:
            root = Path('fixtures/archive-v1')
            card = r.recovery_card(root)
            self.assertEqual('retained-v1-compatibility', card['archive_id'])
            result = r.execute(root, r.plan(root, repository_id='empty', destination=Path(tmp)/'restored'))
            self.assertTrue(result['complete'])
            self.assertEqual('passed', result['git_integrity'])

    def test_format_and_encryption_compatibility_matrix(self):
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-compat-') as tmp:
            root = Path(tmp)
            original = a.read(Path('fixtures/archive-v1'), 'archive.json', 'archive')
            for version in (0, 2, '1', True, None):
                (root/'archive.json').write_text(json.dumps(dict(original, version=version)))
                with self.subTest(version=version), self.assertRaises(a.UnsupportedRecord):
                    r.recovery_card(root)
            for encryption in ('aes', {}, None):
                (root/'archive.json').write_text(json.dumps(dict(original, encryption=encryption)))
                with self.subTest(encryption=encryption), self.assertRaises(a.UnsupportedRecord):
                    r.recovery_card(root)
            (root/'archive.json').write_text(json.dumps(dict(original, future_annotation='ignored')))
            self.assertEqual(1, r.recovery_card(root)['format_version'])

    def test_card_allows_only_nonsecret_location_fields(self):
        root = Path('fixtures/archive-v1')
        for provider in ('r2', 's3', 'b2'):
            location = dict(provider=provider, endpoint='https://storage.example.invalid',
                            region='region', bucket='bucket', prefix='archive/')
            self.assertEqual(location, r.recovery_card(root, location)['storage'])
            for changes in ({'token': 'SECRET'}, {'endpoint': 'https://user:SECRET@host'},
                            {'endpoint': 'https://host?token=SECRET'}):
                with self.assertRaises(ValueError):
                    r.recovery_card(root, dict(location, **changes))


if __name__ == '__main__':
    unittest.main()
