"""Cloud composition fault checks without external storage or credentials."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import gitpickle_archive as a
from gitpickle_cloud import CloudArchive
from gitpickle_jobs import JobError, Jobs
from gitpickle_writer import Writer


class CloudTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated/workflow')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'cloud') + '-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'cache'
        self.root.mkdir()
        self.remote = {}
        self.fail = False
        self.supported = True
        self.env = patch.dict(os.environ, GITPICKLE_STORAGE_CONFIG=json.dumps(dict(
            provider='r2', endpoint='https://example.invalid', region='auto', bucket='owned', prefix='archive/')))
        self.env.start()
        self.addCleanup(self.env.stop)

    def command(self, operation, *args):
        if operation == 'capabilities':
            return json.dumps(dict(conditional_create=self.supported and not getattr(self, 'b2', False),
                archive_publication='content-addressed-v1' if self.supported and getattr(self, 'b2', False) else None,
                completed_write_visibility=True)).encode()
        if operation == 'download':
            for key, data in self.remote.items():
                if not key.startswith('.probes/'):
                    a.put(Path(args[0]), key, data)
        elif operation == 'put':
            key, path = args
            if self.fail:
                raise JobError('storage_unavailable')
            data = Path(path).read_bytes()
            if key in self.remote and self.remote[key] != data:
                raise JobError('conflict')
            self.remote[key] = data
        return b''

    def cloud(self):
        return CloudArchive(self.root, 'unused', command=self.command)

    def test_hosted_push_remote_failure_and_disk_loss(self):
        import time
        import gitpickle_hosted as hosted
        from gitpickle_hosting import Hosting
        from gitpickle_plans import Grant, Plans, Policy, PolicyStore, Principal
        import gitpickle_perf as fixtures
        from gitpickle_receive import packet
        source = self.base / 'push-source'
        fixtures.make_repo(source, 2, 1, 32)
        tip = a.git(source, 'rev-parse', 'refs/heads/main').strip()
        pack = a.git(source, 'pack-objects', '--stdout', '--all')
        request = packet(b'0' * 40 + b' ' + tip + b' refs/heads/main\0report-status side-band-64k\n') + b'0000' + pack
        policy = PolicyStore(Policy(1, tuple(Grant('owner', permission, frozenset({'repo'}), int(time.time()) + 3600)
                            for permission in ('git_read', 'git_write', 'hosting_manage')), frozenset(), str(self.base)))
        principal = Principal('owner')
        with self.cloud() as cloud, Plans(self.root, policy) as plans:
            hosting = Hosting(plans, 'https://example.invalid/git')
            hosted.change(hosting, principal, 'repo', 'create', None)
            before = hosted.head(self.root, 'repo')
            command = cloud.command
            def fail_payload(operation, *arguments):
                if operation == 'put' and '/packs/' in arguments[0]:
                    raise JobError('storage_unavailable')
                return command(operation, *arguments)
            with patch.object(cloud, 'command', fail_payload):
                with self.assertRaises(JobError):
                    hosted.receive(hosting, principal, 'repo', request)
            self.assertEqual(before, hosted.head(self.root, 'repo'))
            result = hosted.receive(hosting, principal, 'repo', request)
            self.assertIn(b'ok refs/heads/main', result)
            committed = hosted.head(self.root, 'repo')
            self.assertIn(committed, self.remote)
        shutil.rmtree(self.root)
        shutil.rmtree(source)
        self.root.mkdir()
        with self.cloud(), Plans(self.root, policy) as plans:
            hosting = Hosting(plans, 'https://example.invalid/git')
            self.assertEqual('hosted', hosting.describe(principal, 'repo')['mode'])
            self.assertEqual(result, hosted.receive(hosting, principal, 'repo', request))
            with hosting.generation(principal, 'repo') as (repo, _):
                self.assertEqual(tip, a.git(repo, 'rev-parse', 'refs/heads/main').strip())
                a.git(repo, 'fsck', '--full', '--strict')

    def test_remote_acceptance_capture_and_rebuild_without_original_disk(self):
        source = self.base / 'source'
        source.mkdir()
        a.git(source, 'init', '--bare', '--template=', '--initial-branch=main')
        with self.cloud() as cloud:
            with Jobs(self.root, disk_reserve=0, source_interval=0) as jobs:
                job = jobs.accept('owner', 'backup', 'capture', 'repo', 'local', {'repo': str(source)})
                jobs.start()
                self.assertEqual('completed', jobs.follow(job['id'])['state'])
                self.assertTrue(any(k.startswith('jobs/' + job['id']) for k in self.remote))
                self.assertTrue(any(k.startswith('repos/repo/commits/') for k in self.remote))
            cloud.probe()
            self.assertTrue(any(k.startswith('.probes/') for k in self.remote))
        shutil.rmtree(self.root)
        shutil.rmtree(source)
        self.root.mkdir()
        with self.cloud():
            with Jobs(self.root) as rebuilt:
                self.assertEqual('completed', rebuilt.inspect(job['id'])['state'])
                point = rebuilt.inspect(job['id'])['result']
                result = a.restore(self.root, point, self.base / 'restored')
                self.assertTrue(result['complete'])
        self.assertEqual([], list(self.root.glob('*-upload-*')))

    def test_remote_failure_cannot_acknowledge_even_when_local_bytes_exist(self):
        with self.cloud():
            key = 'control/' + a.digest(b'data')
            a.durable_put(self.root, key, b'data')
            self.fail = True
            with self.assertRaisesRegex(JobError, 'storage_unavailable'):
                a.durable_put(self.root, key, b'data')
            with self.assertRaisesRegex(JobError, 'storage_unavailable'):
                a.durable_put(self.root, 'control/' + a.digest(b'new'), b'new')
            self.assertFalse((self.root / 'control' / a.digest(b'new')).exists())

    def test_capabilities_and_local_migration_fail_closed(self):
        self.supported = False
        with self.assertRaisesRegex(JobError, 'unsupported_capability'):
            with self.cloud():
                pass
        self.assertEqual({}, self.remote)
        self.supported = True
        a.initialize(self.root, 'local-only')
        with self.assertRaisesRegex(JobError, 'archive_cache_conflict'):
            with self.cloud():
                pass
        self.assertEqual({}, self.remote)


class B2CloudTests(CloudTests):
    """Exercise the complete cloud workflow with no conditional-write capability."""
    def setUp(self):
        super().setUp()
        self.b2 = True
        location = json.loads(os.environ['GITPICKLE_STORAGE_CONFIG'])
        location['provider'] = 'b2'
        os.environ['GITPICKLE_STORAGE_CONFIG'] = json.dumps(location)

    def test_recovered_archive_requires_fencing_before_other_server_writes(self):
        with self.cloud(), Writer(self.root, server_id='original') as writer:
            self.assertTrue(writer.writable)
        shutil.rmtree(self.root)
        self.root.mkdir()
        with self.cloud(), Writer(self.root, server_id='replacement') as writer:
            self.assertFalse(writer.writable)
            with self.assertRaises(PermissionError):
                with writer.mutation('repo'):
                    pass
            writer.takeover(writer.owner_key, 'Old credentials revoked by operator', writer.reconcile())
            self.assertTrue(writer.writable)


if __name__ == '__main__':
    unittest.main()
