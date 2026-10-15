"""Check content integrity and standard descriptors in generated OCI archives."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from gitpickle_oci import convert


class OciTests(unittest.TestCase):
    def fixture(self, root, corrupt=False):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as archive:
            info = tarfile.TarInfo('data')
            info.size = 4
            archive.addfile(info, io.BytesIO(b'data'))
        layer = raw.getvalue()
        config = json.dumps(dict(architecture='amd64', os='linux',
            rootfs=dict(type='layers', diff_ids=['sha256:' + hashlib.sha256(layer).hexdigest()]))).encode()
        manifest = json.dumps([dict(Config='config.json', Layers=['layer.tar'])]).encode()
        source = root / 'docker.tar'
        with tarfile.open(source, 'w') as archive:
            for name, data in [('manifest.json', manifest), ('config.json', config),
                               ('layer.tar', b'corrupt' if corrupt else layer)]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return source

    def test_layout_and_descriptors_match_every_blob(self):
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')) as directory:
            root = Path(directory)
            source = self.fixture(root)
            digest = convert(source, root / 'image.oci.tar')
            with tarfile.open(root / 'image.oci.tar') as archive:
                self.assertEqual({'imageLayoutVersion': '1.0.0'}, json.load(archive.extractfile('oci-layout')))
                index = json.load(archive.extractfile('index.json'))
                self.assertEqual(digest, index['manifests'][0]['digest'])
                def checked(descriptor):
                    data = archive.extractfile('blobs/sha256/' + descriptor['digest'].split(':')[1]).read()
                    self.assertEqual(descriptor['size'], len(data))
                    self.assertEqual(descriptor['digest'], 'sha256:' + hashlib.sha256(data).hexdigest())
                    return data
                manifest = json.loads(checked(index['manifests'][0]))
                checked(manifest['config'])
                for layer in manifest['layers']:
                    checked(layer)
            convert(source, root / 'second.oci.tar')
            self.assertEqual((root / 'image.oci.tar').read_bytes(), (root / 'second.oci.tar').read_bytes())

    def test_corrupt_docker_layer_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')) as directory:
            root = Path(directory)
            source = self.fixture(root, corrupt=True)
            with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
                convert(source, root / 'image.oci.tar')
            self.assertFalse((root / 'image.oci.tar').exists())


if __name__ == '__main__':
    unittest.main()
