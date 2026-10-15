"""Convert a trusted single-image Docker save archive to a pinned OCI archive."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile


def convert(source, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-oci-') as folder:
        root = Path(folder)
        blobs = root / 'blobs/sha256'
        blobs.mkdir(parents=True)
        def blob_bytes(data):
            digest = hashlib.sha256(data).hexdigest()
            (blobs / digest).write_bytes(data)
            return digest, len(data)
        def descriptor(media, digest, size):
            return dict(mediaType=media, digest='sha256:' + digest, size=size)
        with tarfile.open(source) as archive:
            manifest = json.load(archive.extractfile('manifest.json'))
            if len(manifest) != 1:
                raise ValueError('Exactly one image is required')
            saved = manifest[0]
            config = archive.extractfile(saved['Config']).read()
            parsed = json.loads(config)
            config_hash, config_size = blob_bytes(config)
            layers = []
            for index, name in enumerate(saved['Layers']):
                staging = root / 'layer'
                with archive.extractfile(name) as incoming, staging.open('wb') as destination:
                    shutil.copyfileobj(incoming, destination)
                digest = hashlib.sha256()
                with staging.open('rb') as incoming:
                    for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                        digest.update(chunk)
                value = digest.hexdigest()
                if parsed['rootfs']['diff_ids'][index] != 'sha256:' + value:
                    raise ValueError('Docker layer integrity mismatch')
                layers.append(descriptor('application/vnd.oci.image.layer.v1.tar', value, staging.stat().st_size))
                staging.replace(blobs / value)
        manifest = dict(schemaVersion=2, mediaType='application/vnd.oci.image.manifest.v1+json',
            config=descriptor('application/vnd.oci.image.config.v1+json', config_hash, config_size), layers=layers)
        manifest_data = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
        manifest_hash, manifest_size = blob_bytes(manifest_data)
        entry = descriptor(manifest['mediaType'], manifest_hash, manifest_size)
        entry['platform'] = dict(architecture=parsed['architecture'], os=parsed['os'])
        (root / 'index.json').write_text(json.dumps(dict(schemaVersion=2, manifests=[entry]), sort_keys=True) + '\n')
        (root / 'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}\n')
        with tarfile.open(output, 'w', format=tarfile.USTAR_FORMAT) as archive:
            for path in sorted(root.rglob('*')):
                if path.is_file():
                    info = tarfile.TarInfo(str(path.relative_to(root)))
                    info.size, info.mode = path.stat().st_size, 0o644
                    with path.open('rb') as incoming:
                        archive.addfile(info, incoming)
    return 'sha256:' + manifest_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('docker_archive', type=Path)
    parser.add_argument('oci_archive', type=Path)
    args = parser.parse_args()
    print(convert(args.docker_archive, args.oci_archive))


if __name__ == '__main__':
    main()
