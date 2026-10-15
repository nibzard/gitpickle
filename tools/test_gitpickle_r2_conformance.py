"""Exercise ownership checks before live R2 cleanup can delete test data."""
from datetime import datetime, timezone, timedelta
import hashlib
import io
import unittest

import gitpickle_r2_conformance as probe
try:
    from botocore.exceptions import ClientError
except ImportError:
    ClientError = None


class Storage:
    def __init__(self, key, body):
        self.objects = {key: body}
        self.uploads = []
        self.deleted = []
        self.aborted = []

    def get_paginator(self, name):
        assert name == 'list_multipart_uploads'
        return self

    def paginate(self, **kwargs):
        return [{'Uploads': list(self.uploads)}]

    def get_object(self, **kwargs):
        key = kwargs['Key']
        if key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[key])}

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        del self.objects[kwargs['Key']]

    def abort_multipart_upload(self, **kwargs):
        self.aborted.append(kwargs)
        self.uploads = [v for v in self.uploads if v['UploadId'] != kwargs['UploadId']]

    def list_objects_v2(self, **kwargs):
        return {'Contents': [{'Key': k} for k in self.objects if k.startswith(kwargs['Prefix'])]}


@unittest.skipIf(ClientError is None, 'Optional live cleanup tests require botocore')
class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.state = {'object_prefix': 'gitpickle-development/looper_unit/conformance/t007-owned/',
                      'started': self.now.isoformat()}
        self.key = self.state['object_prefix'] + 'a'
        self.storage = Storage(self.key, b'owned')
        self.intent = {'event': 'intent', 'key': self.key, 'method': 'PUT', 'query': {},
                       'sha256': hashlib.sha256(b'owned').hexdigest()}

    def test_exact_delete_and_repeated_cleanup(self):
        probe.remove_owned(self.storage, self.state, [self.intent])
        probe.remove_owned(self.storage, self.state, [self.intent])
        self.assertEqual(self.storage.deleted, [{'Bucket': probe.BUCKET, 'Key': self.key}])

    def test_changed_content_is_preserved(self):
        self.storage.objects[self.key] = b'foreign'
        with self.assertRaises(AssertionError):
            probe.remove_owned(self.storage, self.state, [self.intent])
        self.assertEqual(self.storage.deleted, [])

    def test_outside_prefix_is_preserved(self):
        self.intent['key'] = 'other/a'
        with self.assertRaises(AssertionError):
            probe.remove_owned(self.storage, self.state, [self.intent])
        self.assertEqual(self.storage.deleted, [])

    def test_unjournaled_object_is_preserved(self):
        with self.assertRaises(AssertionError):
            probe.remove_owned(self.storage, self.state, [])
        self.assertEqual(self.storage.deleted, [])

    def test_lost_initiation_uses_exact_key_and_creation_time(self):
        event = dict(self.intent, method='POST', query={'uploads': ''})
        self.storage.objects.clear()
        self.storage.uploads = [{'Key': self.key, 'UploadId': 'owned-id', 'Initiated': self.now}]
        probe.remove_owned(self.storage, self.state, [event])
        probe.remove_owned(self.storage, self.state, [event])
        self.assertEqual(len(self.storage.aborted), 1)

    def test_foreign_upload_key_is_preserved(self):
        self.storage.uploads = [{'Key': self.key + '-foreign', 'UploadId': 'foreign', 'Initiated': self.now}]
        with self.assertRaises(AssertionError):
            probe.remove_owned(self.storage, self.state, [self.intent])
        self.assertEqual(self.storage.aborted, [])

    def test_old_upload_is_preserved(self):
        self.storage.uploads = [{'Key': self.key, 'UploadId': 'old', 'Initiated': self.now - timedelta(days=1)}]
        event = dict(self.intent, method='POST', query={'uploads': ''})
        with self.assertRaises(AssertionError):
            probe.remove_owned(self.storage, self.state, [event])
        self.assertEqual(self.storage.aborted, [])
