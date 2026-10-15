"""Build deterministic release bundles and a CycloneDX dependency inventory."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.check_output(args, cwd=ROOT, **kwargs)


def inventory(metadata):
    components = []
    for package in sorted(metadata['packages'], key=lambda p: (p['name'], p['version'])):
        component = dict(type='library', name=package['name'], version=package['version'],
                         **{'bom-ref': package['id']})
        if package.get('license'):
            component['licenses'] = [dict(expression=package['license'])]
        if package.get('source'):
            component['purl'] = f"pkg:cargo/{package['name']}@{package['version']}"
        components.append(component)
    return dict(bomFormat='CycloneDX', specVersion='1.6', version=1,
                metadata=dict(component=dict(type='application', name='gitpickle',
                    version=tomllib.loads((ROOT / 'Cargo.toml').read_text())['workspace']['package']['version'])),
                components=components)


def bundle(files, output, epoch):
    """Sort members and remove host identity, timestamps, and gzip filenames."""
    with output.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, (data, mode) in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), mode, epoch
                archive.addfile(info, io.BytesIO(data))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / 'Cargo.toml').read_text())['workspace']['package']['version']
    target = run('rustc', '-vV').decode().split('host: ')[1].splitlines()[0]
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '0'))
    environment = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), CARGO_INCREMENTAL='0',
        RUSTFLAGS=f'--remap-path-prefix={ROOT}=/src/gitpickle')
    subprocess.run(['cargo', 'build', '--release', '--locked', '-p', 'gitpickle'],
                   cwd=ROOT, env=environment, check=True)
    metadata = json.loads(run('cargo', 'metadata', '--locked', '--format-version', '1'))
    sbom = inventory(metadata)
    lock = tomllib.loads((ROOT / 'Cargo.lock').read_text())
    checksums = {(p['name'], p['version']): p['checksum'] for p in lock['package'] if 'checksum' in p}
    for component in sbom['components']:
        checksum = checksums.get((component['name'], component['version']))
        if checksum:
            component['hashes'] = [dict(alg='SHA-256', content=checksum)]
    binary = Path(metadata['target_directory']) / 'release/gitpickle'
    files = {'gitpickle': (binary.read_bytes(), 0o755),
             'sbom.cdx.json': ((json.dumps(sbom, indent=2, sort_keys=True) + '\n').encode(), 0o644)}
    for path in ('deploy/systemd/gitpickle.service', 'deploy/compose.yaml', 'docs/deployment.md',
                 'docs/setup.md', 'deploy/nginx-git.conf.example'):
        files[path] = ((ROOT / path).read_bytes(), 0o644)
    notices = ['GitPickle dependency inventory', '',
        'The repository does not yet declare a project distribution license.',
        'This private development bundle grants no additional license rights.',
        'Resolve the project license before public distribution.', '',
        'Runtime dependencies (installed separately for VM bundles):',
        'Git: GPL-2.0-only; Git LFS: MIT; Python 3: PSF-2.0 and bundled notices.',
        'prlimit (util-linux): GPL-2.0-or-later; libc: LGPL-2.1-or-later.',
        'CA certificates: see the installed distribution copyright records.',
        'No walgit code is linked. Rust crates follow their declared expressions.',
        'The SBOM includes the locked workspace graph, including build and platform crates.', '']
    for package in sorted(metadata['packages'], key=lambda p: (p['name'], p['version'])):
        notices.append(f"{package['name']} {package['version']}: {package.get('license') or 'NOASSERTION'}")
        if package.get('source'):
            folder = Path(package['manifest_path']).parent
            candidates = list(folder.glob('LICENSE*')) + list(folder.glob('COPYING*')) + list(folder.glob('NOTICE*'))
            if package.get('license_file'):
                candidates.append(folder / package['license_file'])
            for license_path in candidates:
                if license_path.is_file():
                    name = f"licenses/{package['name']}-{package['version']}/{license_path.name}"
                    files[name] = (license_path.read_bytes(), 0o644)
    files['NOTICES.txt'] = (('\n'.join(notices) + '\n').encode(), 0o644)
    provenance = dict(version=version, target=target, source_date_epoch=epoch,
        rustc=run('rustc', '-vV').decode(), cargo_lock_sha256=hashlib.sha256((ROOT / 'Cargo.lock').read_bytes()).hexdigest(),
        binary_sha256=hashlib.sha256(files['gitpickle'][0]).hexdigest(),
        source_sha256=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(
            [*ROOT.glob('crates/**/*.rs'), *ROOT.glob('tools/gitpickle_*.py'), ROOT / 'Cargo.toml', ROOT / 'Cargo.lock']))).hexdigest(),
        build_platform=platform.system())
    files['build.json'] = ((json.dumps(provenance, sort_keys=True, indent=2) + '\n').encode(), 0o644)
    name = f'gitpickle-{version}-{target}.tar.gz'
    bundle(files, output / name, epoch)
    (output / 'SHA256SUMS').write_text(hashlib.sha256((output / name).read_bytes()).hexdigest() + '  ' + name + '\n')
    # A small, explicit build context for the runtime image stage.
    for key in ('gitpickle', 'sbom.cdx.json', 'NOTICES.txt'):
        (output / key).write_bytes(files[key][0])
    for key, (data, _) in files.items():
        if key.startswith('licenses/'):
            destination = output / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    (output / 'gitpickle').chmod(0o755)
    print(output / name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
