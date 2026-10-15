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
            return json.dumps(dict(conditional_create=self.supported, completed_write_visibility=True)).encode()
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


if __name__ == '__main__':
    unittest.main()
