"""Cleanup rejects foreign scope and changed bytes, including repeated cleanup."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import gitpickle_portfolio_recovery as acceptance


class Storage:
    def __init__(self, objects):
        self.objects = objects
        self.deleted = []

    def get_object(self, *, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket, Key):
        self.deleted.append(Key)
        del self.objects[Key]

    def list_objects_v2(self, *, Bucket, Prefix):
        return {'Contents': [key for key in self.objects if key.startswith(Prefix)]}

    def list_multipart_uploads(self, **kwargs):
        return {}


class CleanupTests(unittest.TestCase):
    def setUp(self):
        try:
            import botocore.exceptions  # Optional live-provider dependency.
        except ImportError:
            self.skipTest('Boto3 is required for live cleanup tests')
        self.temp = tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 't022') + '-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.receipt = self.base / 'receipt.json'
        self.journal = self.base / 'journal.jsonl'
        self.prefix = 'owned'
        self.run = 't022-' + 'a' * 24
        self.object_prefix = acceptance.BASE + self.prefix + '/recovery/' + self.run + '/'
        self.state = dict(resource_prefix=self.prefix, uid=os.getuid(), run=self.run,
            object_prefix=self.object_prefix, bucket=acceptance.BUCKET,
            endpoint=acceptance.ENDPOINT, journal=str(self.journal))
        acceptance.save(self.receipt, self.state)
        self.key = self.object_prefix + 'repos/r/packs/abc'
        self.body = b'owned bytes'
        self.journal.write_text(json.dumps(dict(key=self.key,
            sha256=acceptance.hashlib.sha256(self.body).hexdigest())) + '\n')
        self.storage = Storage({self.key: self.body, 'unrelated/object': b'preserve'})

    def clean(self):
        acceptance.cleanup(self.receipt, self.prefix, self.storage)

    def test_repeated_cleanup_preserves_other_resources(self):
        self.clean()
        self.clean()
        self.assertEqual([self.key], self.storage.deleted)
        self.assertEqual({'unrelated/object': b'preserve'}, self.storage.objects)

    def test_changed_content_stops_removal(self):
        self.storage.objects[self.key] = b'foreign content'
        with self.assertRaises(AssertionError):
            self.clean()
        self.assertEqual([], self.storage.deleted)

    def test_foreign_journal_key_stops_before_access(self):
        self.journal.write_text(json.dumps(dict(key='unrelated/object', sha256='a' * 64)) + '\n')
        with self.assertRaises(AssertionError):
            self.clean()
        self.assertEqual([], self.storage.deleted)

    def test_wrong_operation_stops_before_access(self):
        self.state['resource_prefix'] = 'foreign'
        acceptance.save(self.receipt, self.state)
        with self.assertRaises(AssertionError):
            self.clean()
        self.assertEqual([], self.storage.deleted)

    def test_unknown_objects_are_retained(self):
        key = self.object_prefix + 'unknown'
        self.storage.objects[key] = b'not journaled'
        with self.assertRaises(AssertionError):
            self.clean()
        self.assertIn(key, self.storage.objects)


if __name__ == '__main__':
    unittest.main()
