"""Read-only storage inventory, recovery planning, and full Git bundle export."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import gitpickle_archive as a


def recovery_card(root, location=None):
    """Build a nonsecret card from verified identity and explicit storage fields."""
    archive = a.read(root, 'archive.json', 'archive')
    if location is None:
        location = dict(provider='filesystem', path=str(Path(root).resolve()))
    location = dict(location)
    provider = location.get('provider')
    fields = {'provider', 'path'} if provider == 'filesystem' else {
        'provider', 'endpoint', 'region', 'bucket', 'prefix'}
    if (provider not in ('filesystem', 'r2', 's3', 'b2') or set(location) != fields
            or any(not isinstance(v, str) or not v or any(ord(c) < 32 for c in v)
                   for v in location.values())):
        raise ValueError('invalid recovery location')
    if provider != 'filesystem':
        endpoint = urlsplit(location['endpoint'])
        if (endpoint.scheme not in ('http', 'https') or not endpoint.hostname
                or endpoint.username is not None or endpoint.password is not None
                or endpoint.query or endpoint.fragment):
            raise ValueError('recovery endpoint must not contain credentials')
    return dict(card_version=1, archive_id=archive['archive_id'],
        root_digest=a.digest(a.read(root, 'archive.json')), format_version=archive['version'],
        storage=location, client_side_encryption='none', required_application_keys=[],
        key_location_guidance='Keep storage credentials and any provider-managed key access in a separate recovery vault. '
            'Test access from another machine. No key may exist only on the daemon.',
        software=dict(reader_format_versions=[1],
            retrieval='Use your independently retained GitPickle executable, or build your retained source with '
                'cargo build --locked --release. Install Python 3.10 or later and native Git. '
                'See the retained docs/recovery.md and docs/archive-format/README.md.',
            retention='Retain the executable for your recovery platform, its checksum, source, Cargo.lock, '
                'toolchain, dependencies, and format documentation outside the daemon and archive account.'),
        limits='Only captured and retained data can be recovered. Missing LFS, unavailable wikis, '
            'unpushed work, and GitHub platform metadata are not implicitly protected. '
            'Losing storage access or required provider keys can prevent recovery.')


def point_info(root, key, repository_id=None):
    p = a.read(root, key, 'point')
    if (not isinstance(p['refs'], dict) or not isinstance(p['head'], dict)
            or not isinstance(p['packs'], list) or not isinstance(p['lfs'], dict)
            or not isinstance(p['objects'], list)):
        raise ValueError('invalid point fields')
    identity = a.read(root, p['identity'], 'identity')
    archive = a.read(root, 'archive.json', 'archive')
    repo_id = identity['repository_id']
    if (not re.fullmatch('[A-Za-z0-9_-]+', repo_id)
            or identity['archive_id'] != archive['archive_id']
            or repository_id is not None and repo_id != repository_id
            or not key.startswith(f'repos/{repo_id}/points/')
            or not p['identity'].startswith(f'repos/{repo_id}/identity/')):
        raise ValueError('point identity mismatch')
    if p['hash_algorithm'] not in ('sha1', 'sha256'):
        raise ValueError('unsupported Git hash algorithm')
    if not isinstance(p['coverage'], dict) or not {'git', 'lfs'} <= p['coverage'].keys():
        raise ValueError('missing component coverage')
    missing = []
    byte_count = 0
    dependencies = [(obj, 'packs') for obj in p['packs']]
    for oid, obj in p['lfs'].items():
        if not re.fullmatch('[0-9a-f]{64}', oid) or obj['key'].split('/')[-1] != oid:
            raise ValueError('LFS identity mismatch')
        dependencies.append((obj, 'lfs'))
    for obj, component in dependencies:
        if (not obj['key'].startswith(f'repos/{repo_id}/{component}/')
                or type(obj['size']) is not int or obj['size'] < 0):
            raise ValueError('invalid dependency')
        byte_count += obj['size']
        try:
            payload = a.read(root, obj['key'])
        except FileNotFoundError:
            missing.append(obj['key'])
            continue
        if len(payload) != obj['size']:
            raise ValueError('object size mismatch')
    if 'wiki' in p and 'wiki' not in p['coverage']:
        raise ValueError('missing wiki coverage')
    wiki_complete = True
    if p.get('wiki') is not None and p.get('wiki') is not False:
        a.wiki_manifest(root, repo_id, p['wiki'])
        child = point_info(root, p['wiki'], repo_id + '-wiki')
        missing.extend(child['missing'])
        byte_count += child['bytes']
        wiki_complete = child['complete']
    elif p['coverage'].get('wiki') == 'complete':
        raise ValueError('manifest omits required wiki')
    complete = wiki_complete and not missing and all(v == 'complete' for v in p['coverage'].values())
    return {'point': key, 'repository_id': repo_id, 'source': identity['source'],
            'coverage': p['coverage'], 'complete': complete, 'missing': missing,
            'bytes': byte_count, 'refs': p['refs'], 'head': p['head'], 'verified': False}


def storage_inventory(root, repository_ids=None):
    """Rebuild from immutable publications and matching evidence. Write no cache."""
    root = Path(root)
    archive = a.read(root, 'archive.json', 'archive')
    repos = root / 'repos'
    if repos.is_symlink():
        raise ValueError('symlink in archive')
    result = []
    for directory in sorted(repos.iterdir()) if repos.exists() else []:
        repo_id = directory.name
        if repository_ids is not None and repo_id not in repository_ids:
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('invalid repository directory')
        chain = a.inventory(root, repo_id)
        points = [point_info(root, a.read(root, c, 'publication')['point'], repo_id) for c in chain]
        by_key = {p['point']: p for p in points}
        components = {key: set() for key in by_key}
        checks = directory / 'checks'
        if checks.is_symlink():
            raise ValueError('symlink in archive')
        for path in sorted(checks.iterdir()) if checks.exists() else []:
            evidence = a.read(root, f'repos/{repo_id}/checks/{path.name}', 'evidence')
            if (evidence['archive_id'] != archive['archive_id']
                    or evidence['repository_id'] != repo_id or evidence['point'] not in by_key):
                raise ValueError('verification identity mismatch')
            if (evidence['passed'] is True and evidence['reader_version'] == 1
                    and evidence['method'] in ('archive_readback', 'git_integrity', 'lfs_digest', 'independent_restore')):
                allowed = {'lfs'} if evidence['method'] == 'lfs_digest' else (
                    {'git', 'wiki'} if evidence['method'] == 'git_integrity' else {'git', 'lfs', 'wiki'})
                components[evidence['point']].update(set(evidence['components']) & allowed)
        for point in points:
            point['verified'] = point['complete'] and set(point['coverage']) <= components[point['point']]
        if points:
            result.append({'repository_id': repo_id, 'points': points})
    return {'version': 1, 'archive_id': archive['archive_id'], 'repositories': result}


def plan(root, repository_id=None, point=None, destination=None, allow_incomplete=False, bundle=False):
    if allow_incomplete and point is None:
        raise ValueError('--allow-incomplete requires --point')
    if point is not None:
        selected = point_info(root, point, repository_id)
        # Explicit disaster recovery can read a point even with a damaged publication chain.
        selection = 'explicit'
    else:
        inventory = storage_inventory(root)
        repositories = inventory['repositories']
        if repository_id is None:
            if len(repositories) != 1:
                raise ValueError('select --repository from the storage inventory')
            repository_id = repositories[0]['repository_id']
        candidates = next((r['points'] for r in repositories if r['repository_id'] == repository_id), [])
        selected = next((p for p in reversed(candidates) if p['complete'] and p['verified']), None)
        if selected is None:
            raise ValueError('no complete verified point; inspect --inventory or explicitly select --point')
        selection = 'newest_complete_verified'
    warnings = []
    if not selected['complete']:
        if not allow_incomplete:
            raise ValueError('incomplete point requires --point and --allow-incomplete')
        warnings.append('Incomplete recovery: component coverage or required data is missing.')
        warnings.extend('Missing: ' + key for key in selected['missing'])
    if selection == 'explicit':
        warnings.append('Explicit point selected; prior verification is not assumed. Execution validates archive data.')
    if destination is None:
        stem = selected['repository_id'] + '-restored-' + selected['point'].split('/')[-1][:12]
        if bundle:
            stem += '-bundle'
        destination = Path.cwd() / stem
        index = 1
        while destination.exists() or destination.is_symlink():
            destination = Path.cwd() / (stem + '-' + str(index))
            index += 1
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination exists')
    return dict(selected, version=1, selection=selection, destination=str(destination),
                warnings=warnings, git_integrity='not_run', export='full_bundle' if bundle else 'bare_repository')


def execute(root, recovery_plan, allow_incomplete=False, *, _pinned=False):
    if not _pinned:
        with a.security.destination_parent(recovery_plan['destination']) as pinned:
            selected = dict(recovery_plan, destination=str(pinned))
            result = execute(root, selected, allow_incomplete, _pinned=True)
            result['destination'] = recovery_plan['destination']
            return result
    destination = Path(recovery_plan['destination'])
    if recovery_plan['export'] == 'bare_repository':
        result = a.restore(root, recovery_plan['point'], destination, allow_incomplete, _pinned=True)
    else:
        if destination.exists() or destination.is_symlink():
            raise ValueError('destination exists')
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-bundle-',
                                         dir=destination.parent) as staging:
            staging = Path(staging)
            repo = staging / 'repository.git'
            result = a.restore(root, recovery_plan['point'], repo, allow_incomplete, _pinned=True)
            refs = a.git(repo, 'for-each-ref', '--format=%(refname)')
            try:
                a.git(repo, 'rev-parse', '--verify', 'HEAD')
                has_head = True
            except subprocess.CalledProcessError:
                has_head = False
            if not refs and not has_head:
                raise ValueError('empty repository cannot form a Git bundle; export a bare repository')
            output = staging / 'export'
            output.mkdir(mode=0o700)
            bundle = output / 'repository.bundle'
            a.git(repo, 'bundle', 'create', str(bundle), '--all')
            # Verify in an empty repository: a full bundle cannot need prerequisite objects.
            verifier = staging / 'verify.git'
            verifier.mkdir()
            algorithm = a.read(Path(root), recovery_plan['point'], 'point')['hash_algorithm']
            a.git(verifier, 'init', '--bare', '--template=', '--object-format='+algorithm)
            a.git(verifier, 'bundle', 'verify', str(bundle))
            metadata = dict(result, bundle_kind='full', prerequisites=[], refs=recovery_plan['refs'],
                            head=recovery_plan['head'], lfs_included=False, wiki_included=False,
                            note='Git bundle only. Recover required LFS and wiki separately with bare repository export.')
            (output / 'export.json').write_text(json.dumps(metadata, sort_keys=True)+'\n')
            a.install_directory(output, destination)
        result['bundle_kind'] = 'full'
        result['lfs_included'] = False
        result['wiki_included'] = False
    output = dict(recovery_plan, **result)
    output['warnings'] = list(recovery_plan['warnings'])
    if not result['complete'] and not any('Incomplete recovery' in w for w in output['warnings']):
        output['warnings'].append('Incomplete recovery: component coverage or required data is missing.')
    return output


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError('invalid input')
    parser = Parser(description=__doc__)
    parser.add_argument('--from-storage', required=True, nargs='?', const=os.environ.get('GITPICKLE_ARCHIVE'), metavar='ARCHIVE', help='Read-only archive directory')
    parser.add_argument('--recovery-card', action='store_true', help='Print a nonsecret recovery card as JSON')
    parser.add_argument('--archive-id', help='Expected archive identity from your recovery card')
    parser.add_argument('--root-digest', help='Expected SHA-256 digest of archive.json')
    parser.add_argument('--repository')
    parser.add_argument('--point', help='Explicit immutable point key, including its digest')
    parser.add_argument('--destination', help='New directory; existing destinations are rejected')
    parser.add_argument('--allow-incomplete', action='store_true')
    parser.add_argument('--dry-run', '--preview', action='store_true')
    parser.add_argument('--inventory', action='store_true')
    parser.add_argument('--bundle', action='store_true', help='Export a full Git bundle and export.json in a new directory')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--yes', action='store_true')
    args = argparse.Namespace(json='--json' in (sys.argv[1:] if argv is None else argv), repository=None)
    try:
        args = parser.parse_args(argv)
        if not args.from_storage:
            raise ValueError('archive location required')
        root = Path(args.from_storage)
        archive = a.read(root, 'archive.json', 'archive')
        if args.archive_id is not None and archive['archive_id'] != args.archive_id:
            raise ValueError('archive identity does not match recovery card')
        if args.root_digest is not None and a.digest(a.read(root, 'archive.json')) != args.root_digest:
            raise ValueError('archive root digest does not match recovery card')
        if args.recovery_card:
            location = json.loads(os.environ['GITPICKLE_RECOVERY_LOCATION']) if 'GITPICKLE_RECOVERY_LOCATION' in os.environ else None
            result = recovery_card(root, location)
        elif args.inventory:
            result = storage_inventory(root)
        else:
            result = plan(root, args.repository, args.point, args.destination, args.allow_incomplete, args.bundle)
            for warning in result['warnings']:
                print(warning, file=sys.stderr)
            print('Recovery plan (UTC): ' + json.dumps(result, sort_keys=True), file=sys.stderr)
            if not args.dry_run:
                if sys.stdin.isatty() and not args.json and not args.yes:
                    print('Restore into this new directory? [y/N] ', end='', file=sys.stderr, flush=True)
                    if input().strip().lower() not in ('y', 'yes'):
                        raise ValueError('confirmation required')
                result = execute(root, result, args.allow_incomplete)
        if args.json:
            result = dict(schema_version=1, observed_at_unix_seconds=int(time.time()),
                scope=dict(archive_id=archive['archive_id'], repository_ids=[args.repository] if args.repository else []),
                outcome='ok', job_id=None, coverage_gaps=[], evidence_ids=[], next_action=None, error=None, data=result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        # Do not expose transport credentials or untrusted Git stderr.
        safe_messages = {'archive root digest does not match recovery card',
                         'archive identity does not match recovery card',
                         'destination exists', '--allow-incomplete requires --point'}
        message = str(error) if str(error) in safe_messages else 'Archive validation or recovery failed.'
        if args.json:
            result = dict(schema_version=1, observed_at_unix_seconds=int(time.time()),
                scope=dict(archive_id=None, repository_ids=[args.repository] if args.repository else []),
                outcome='failed', job_id=None, coverage_gaps=[], evidence_ids=[], next_action=None, data=None,
                error=dict(code='recovery_failed', message=message, retry={'kind': 'never'}))
            print(json.dumps(result, sort_keys=True))
        print(json.dumps({'version': 1, 'error': 'recovery_failed', 'detail': message}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
