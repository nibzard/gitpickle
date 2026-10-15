"""Verify that fixture cleanup rejects foreign resources and tolerates absence."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from botocore.exceptions import ClientError
except ImportError:
    ClientError = None
import gitpickle_test_resources as resources


class Objects:
    def __init__(self, marker, body):
        self.marker, self.body = marker, body
        self.deleted = []

    def get_object(self, **kwargs):
        import io
        if self.body is None:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.body), 'Metadata': {'gitpickle-owner': self.marker}, 'ETag': 'version1'}

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        self.body = None


@unittest.skipIf(ClientError is None, 'Optional live-resource tests require boto3/botocore')
class CleanupTests(unittest.TestCase):
    def setUp(self):
        prefix, nonce = 'looper_test', '0123456789ab'
        self.state = dict(resource_prefix=prefix, nonce=nonce,
                          name='gitpickle-looper-test-' + nonce,
                          marker='GitPickle disposable T004 ' + prefix + ' ' + nonce,
                          object_prefix=resources.BASE + prefix + '/t004-' + nonce + '/',
                          started='2026-10-15T00:00:00Z', repo_id=123)
        self.state['key'] = self.state['object_prefix'] + 'probe.txt'
        self.body = b'synthetic probe'
        self.state['body_sha256'] = hashlib.sha256(self.body).hexdigest()

    def test_foreign_object_marker_is_preserved(self):
        storage = Objects('foreign', self.body)
        with self.assertRaises(AssertionError):
            resources.remove_object(storage, self.state)
        self.assertEqual(storage.deleted, [])

    def test_changed_object_body_is_preserved(self):
        storage = Objects(self.state['marker'], b'changed')
        with self.assertRaises(AssertionError):
            resources.remove_object(storage, self.state)
        self.assertEqual(storage.deleted, [])

    def test_delete_is_exact_and_idempotent(self):
        storage = Objects(self.state['marker'], self.body)
        resources.remove_object(storage, self.state)
        resources.remove_object(storage, self.state)
        self.assertEqual(storage.deleted, [dict(Bucket=resources.BUCKET, Key=self.state['key'])])

    def test_scope_tampering_is_rejected(self):
        self.state['key'] = 'unrelated/data'
        with self.assertRaises(AssertionError):
            resources.validate_state(self.state, 'looper_test')

    def test_foreign_repository_is_preserved(self):
        repo = dict(owner={'id': resources.OWNER_ID}, private=True,
                    name=self.state['name'], description='foreign', id=123,
                    created_at='2026-10-15T01:00:00Z')
        with tempfile.TemporaryDirectory(prefix='gitpickle-' + os.environ.get('LOOPER_RESOURCE_PREFIX', 'unit') + '-') as directory:
            receipt = Path(directory) / 'receipt.json'
            resources.save(receipt, self.state)
            with patch.object(resources, 'api', side_effect=lambda path, *args: dict(login=resources.OWNER, id=resources.OWNER_ID) if path == 'user' else repo), \
                 patch.object(resources, 'command') as command, \
                 patch.object(resources, 'client', return_value=Objects('', None)):
                with self.assertRaises(RuntimeError):
                    resources.cleanup(receipt, 'looper_test')
                command.assert_not_called()

    def test_cleanup_absence_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='gitpickle-' + os.environ.get('LOOPER_RESOURCE_PREFIX', 'unit') + '-') as directory:
            receipt = Path(directory) / 'receipt.json'
            resources.save(receipt, self.state)
            with patch.object(resources, 'api', side_effect=lambda path, *args: dict(login=resources.OWNER, id=resources.OWNER_ID) if path == 'user' else None), \
                 patch.object(resources, 'command') as command, \
                 patch.object(resources, 'client', return_value=Objects('', None)):
                resources.cleanup(receipt, 'looper_test')
                resources.cleanup(receipt, 'looper_test')
                command.assert_not_called()
                self.assertEqual(json.loads(receipt.read_text())['github_cleanup'], 'absent')


if __name__ == '__main__':
    unittest.main()
