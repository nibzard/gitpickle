"""Archive v1 reference prototype. No daemon state or provider SDK is required."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
import gitpickle_security as security


class ArchiveConflict(ValueError):
    """Publication history has conflicting or disconnected records."""


class UnsupportedRecord(ValueError):
    """The archive requires a different reader or capability."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args, data=None):
    env = security.git_environment()
    options = list(security.GIT_OPTIONS)
    if args and args[0] == 'clone':
        # The archive adapter only clones local operator-owned sources. Network
        # access belongs to the authenticated GitHub adapter.
        source = security.local_source(args[-2])
        args = (*args[:-2], source, args[-1])
        env['GIT_ALLOW_PROTOCOL'] = 'file'
        options += ['-c', 'protocol.file.allow=always']
    return security.run(['git', *options, '-C', str(repo), *args],
                        input=data, env=env).stdout


def encode(record):
    return json.dumps(dict(record, version=1), sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def put(root, key, data):
    root = Path(root)
    path = security.archive_path(root, key)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    import tempfile
    fd, staging = tempfile.mkstemp(prefix='.staging-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staging, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError('immutable object conflict')
        parent = path.parent
        while True:
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if parent == root:
                break
            parent = parent.parent
    finally:
        os.unlink(staging)


def record(root, prefix, value):
    data = encode(value)
    key = prefix + '/' + digest(data)
    durable_put(root, key, data)
    return key


def durable_put(root, key, data):
    """Reconcile a lost write response using exact identity and archive digest."""
    try:
        put(root, key, data)
    except (TimeoutError, ConnectionError):
        # A failed read preserves the unknown outcome. Never assume absence.
        # Local I/O and fsync errors are failures, even when bytes are visible.
        if read(root, key) != data:
            raise ValueError('ambiguous write conflicts with expected content')
    if read(root, key) != data:
        raise ValueError('write readback mismatch')


def boundary(name):
    """Fault injection boundary; tests replace this function."""


def timestamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def rebuild_projection(root, repo_id):
    """Local state is disposable. Only publication records select a point."""
    chain = inventory(root, repo_id)
    value = {'publications': chain, 'point': read(root, chain[-1], 'publication')['point'] if chain else None}
    cache = root / '.projection'
    cache.mkdir(exist_ok=True)
    fd, path = tempfile.mkstemp(dir=cache)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode(value))
        os.replace(path, cache / repo_id)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    return value


def observe_and_verify(root, repo_id, point, operation_id=None, observed_at=None):
    archive_id = read(root, 'archive.json', 'archive')['archive_id']
    components = sorted(read(root, point, 'point')['coverage'])
    if observed_at is not None:
        boundary('before_observation')
        record(root, 'control', {'kind': 'control', 'archive_id': archive_id,
               'operation_id': operation_id, 'record_type': 'source_observation',
               'recorded_at': observed_at, 'predecessor': None,
               'payload': {'repository_id': repo_id, 'point': point, 'components': components}})
        boundary('after_observation')
    boundary('before_projection')
    rebuild_projection(root, repo_id)
    boundary('after_projection')
    record(root, 'control', {'kind': 'control', 'archive_id': archive_id,
           'operation_id': operation_id, 'record_type': 'capture_completed',
           'recorded_at': timestamp(), 'predecessor': None,
           'payload': {'repository_id': repo_id, 'point': point, 'components': components}})
    boundary('before_readback')
    with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-', dir=root) as staging:
        result = restore(root, point, Path(staging) / 'readback.git', allow_incomplete=True)
    boundary('after_readback')
    record(root, f'repos/{repo_id}/checks', {'kind': 'evidence', 'archive_id': archive_id,
           'repository_id': repo_id, 'point': point, 'recorded_at': timestamp(),
           'method': 'archive_readback', 'components': result['verified_components'], 'passed': True,
           'reader_version': 1, 'details': 'Archive-only reconstruction and full Git integrity check'})
    boundary('after_evidence')


def read(root, key, kind=None):
    path = security.archive_path(root, key)
    if kind is not None and path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('record exceeds limit')
    data = path.read_bytes()
    if key != 'archive.json' and digest(data) != key.split('/')[-1]:
        raise ValueError('record digest mismatch')
    if kind is None:
        return data
    value = json.loads(data)
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value.get('version') != 1 or value.get('kind') != kind):
        raise UnsupportedRecord('unsupported record')
    if kind == 'archive' and value.get('encryption') != 'none':
        raise UnsupportedRecord('unsupported encryption')
    return value


def initialize(root, archive_id):
    root = Path(root)
    put(root, 'archive.json', encode({'kind': 'archive', 'archive_id': archive_id, 'encryption': 'none'}))


def inventory(root, repo_id):
    if not re.fullmatch('[A-Za-z0-9_-]+', repo_id):
        raise ValueError('invalid repository identity')
    archive = read(root, 'archive.json', 'archive')
    commits = {}
    prefix = f'repos/{repo_id}/commits'
    for path in sorted((root / prefix).glob('*')):
        key = prefix + '/' + path.name
        c = read(root, key, 'publication')
        if c['archive_id'] != archive['archive_id'] or c['repository_id'] != repo_id:
            raise ValueError('publication identity mismatch')
        if c.get('writer') is not None:
            owner = read(root, c['writer'], 'writer')
            if owner['archive_id'] != archive['archive_id']:
                raise ValueError('publication writer mismatch')
        point = read(root, c['point'], 'point')
        ident = read(root, point['identity'], 'identity')
        if ident['repository_id'] != repo_id or ident['archive_id'] != archive['archive_id'] or point['parent_publication'] != c['parent']:
            raise ValueError('point identity or predecessor mismatch')
        commits[key] = c
    children = {}
    for key, c in commits.items():
        parent = c['parent']
        if parent is not None and parent not in commits:
            raise ArchiveConflict('missing publication predecessor')
        if parent in children:
            raise ArchiveConflict('publication fork')
        children[parent] = key
    ordered, parent = [], None
    while parent in children:
        parent = children[parent]
        ordered.append(parent)
    if len(ordered) != len(commits):
        raise ArchiveConflict('publication cycle')
    return ordered


def capture(root, repo, repo_id, source, lfs=None, operation_id=None, wiki=None):
    from gitpickle_writer import Writer
    with Writer(root) as writer:
        return writer.capture(repo, repo_id, source, lfs, operation_id, wiki=wiki)


def reconcile_capture(root, repo_id, source, operation_id):
    """Resolve a repeated capture without consulting the source or local state."""
    source = security.source_label(source)
    chain = inventory(root, repo_id)
    if chain:
        # A prior process can exit after linking a publication but before the
        # directory flush. Reflush its exact bytes before reusing that outcome.
        durable_put(root, chain[-1], read(root, chain[-1]))
    archive_id = read(root, 'archive.json', 'archive')['archive_id']
    observations = set()
    for path in (root / 'control').glob('*'):
        value = read(root, 'control/' + path.name, 'control')
        if value['operation_id'] != operation_id or value['payload'].get('repository_id') != repo_id:
            continue
        if value['archive_id'] != archive_id:
            raise ValueError('capture archive identity mismatch')
        if value['record_type'] == 'capture_intent' and value['payload']['source'] != source:
            raise ValueError('capture operation reused with a different source')
        if value['record_type'] == 'source_observation':
            observations.add(value['payload']['point'])
    for key in chain:
        publication = read(root, key, 'publication')
        if publication.get('operation_id') == operation_id:
            observations.add(publication['point'])
    if len(observations) > 1:
        raise ValueError('conflicting capture outcomes')
    if observations:
        point = observations.pop()
        if point not in {read(root, key, 'publication')['point'] for key in chain}:
            raise ValueError('capture observation has no publication')
        identity = read(root, read(root, point, 'point')['identity'], 'identity')
        if identity['source'] != source:
            raise ValueError('capture operation reused with a different source')
        return point
    return None


def _capture(root, repo, repo_id, source, lfs=None, writer=None, operation_id=None, wiki=None):
    source = security.source_label(source)
    root = Path(root)
    operation_id = str(uuid.uuid4()) if operation_id is None else operation_id
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 256:
        raise ValueError('invalid capture operation identity')
    if writer is not None:
        durable_put(root, writer, read(root, writer))
    point = reconcile_capture(root, repo_id, source, operation_id)
    if point is not None:
        # Replay grants no new source freshness. Verification still reads archive bytes.
        observe_and_verify(root, repo_id, point)
        return point
    boundary('before_intent')
    record(root, 'control', {'kind': 'control',
           'archive_id': read(root, 'archive.json', 'archive')['archive_id'],
           'operation_id': operation_id, 'record_type': 'capture_intent',
           'recorded_at': timestamp(), 'predecessor': None,
           'payload': {'repository_id': repo_id, 'source': source}})
    boundary('after_intent')
    # Resolve filesystem paths before changing Git's working directory.
    # Network sources are fetched only by the authenticated GitHub adapter.
    repo = str(Path(repo).resolve()) if Path(repo).exists() else str(repo)
    # No hardlinks, alternates, or source configuration enter private staging.
    with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-', dir=root) as staging:
        target = Path(staging) / 'capture.git'
        boundary('before_capture')
        git(root, 'clone', '--mirror', '--no-local', '--template=', repo, str(target))
        # Native clone preserves symbolic or detached HEAD, including unborn HEAD.
        git(target, 'fsck', '--full', '--strict')
        observed_at = timestamp()
        boundary('after_capture')
        point = _publish(root, target, repo_id, source, lfs, writer, operation_id, wiki)
        observe_and_verify(root, repo_id, point, operation_id, observed_at)
        boundary('before_reply')
        return point


def required_lfs(repo, objects=None):
    """Discover pointers in all reachable history without following submodules."""
    if objects is None:
        tips = git(repo, 'for-each-ref', '--format=%(objectname)').decode().split()
        try:
            tips.append(git(repo, 'rev-parse', '--verify', 'HEAD').decode().strip())
        except subprocess.CalledProcessError:
            pass
        objects = git(repo, 'rev-list', '--objects', '--no-object-names', '--stdin',
                      data=('\n'.join(tips)+'\n').encode()).decode().split() if tips else []
    required = {}
    for oid in sorted(objects):
        if git(repo, 'cat-file', '-t', oid).strip() != b'blob':
            continue
        if int(git(repo, 'cat-file', '-s', oid)) > 1024:
            continue
        blob = git(repo, 'cat-file', 'blob', oid)
        if not blob.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            continue
        match = re.fullmatch(rb'version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?', blob)
        if not match:
            raise ValueError('unsupported LFS pointer')
        lfs_oid, size = match[1].decode(), int(match[2])
        if lfs_oid in required and required[lfs_oid] != size:
            raise ValueError('conflicting LFS pointer sizes')
        required[lfs_oid] = size
    return required


def wiki_manifest(root, repo_id, key):
    if not isinstance(key, str) or not key.startswith(f'repos/{repo_id}-wiki/points/'):
        raise ValueError('wiki identity mismatch')
    child = read(root, key, 'point')
    identity = read(root, child['identity'], 'identity')
    if identity['repository_id'] != repo_id + '-wiki' or 'wiki' in child['coverage'] or 'wiki' in child:
        raise ValueError('invalid nested wiki')
    return child


def _publish(root, repo, repo_id, source, lfs=None, writer=None, operation_id=None, wiki=None):
    root, repo = Path(root), Path(repo)
    if not re.fullmatch('[A-Za-z0-9_-]+', repo_id):
        raise ValueError('invalid repository identity')
    archive = read(root, 'archive.json', 'archive')
    chain = inventory(root, repo_id)
    prefix = f'repos/{repo_id}'
    boundary('before_identity')
    identity = record(root, prefix + '/identity', {'kind': 'identity', 'repository_id': repo_id,
                      'archive_id': archive['archive_id'], 'source': source})
    boundary('after_identity')
    refs = dict(line.split(' ', 1) for line in git(repo, 'for-each-ref', '--format=%(refname) %(objectname)').decode().splitlines())
    try:
        head = {'symbolic': git(repo, 'symbolic-ref', 'HEAD').decode().strip()}
    except subprocess.CalledProcessError:
        head = {'detached': git(repo, 'rev-parse', 'HEAD').decode().strip()}
    tips = list(refs.values()) + ([head['detached']] if 'detached' in head else [])
    objects = set(git(repo, 'rev-list', '--objects', '--no-object-names', '--stdin', data=('\n'.join(tips)+'\n').encode()).decode().split()) if tips else set()
    packs, known = [], set()
    if chain:
        previous = read(root, read(root, chain[-1], 'publication')['point'], 'point')
        packs = previous['packs']
        known = set(previous['objects'])
    new = sorted(objects - known)
    if new:
        payload = git(repo, 'pack-objects', '--stdout', '--window=0', data=('\n'.join(new)+'\n').encode())
        key = prefix + '/packs/' + digest(payload)
        boundary('before_payload')
        durable_put(root, key, payload)
        boundary('after_payload')
        packs = packs + [{'key': key, 'size': len(payload)}]
    required = {}
    missing_lfs = []
    for oid_lfs, size in required_lfs(repo, objects).items():
        key = prefix + '/lfs/' + oid_lfs
        required[oid_lfs] = {'key': key, 'size': size}
        if (root / key).exists():
            payload = read(root, key)
            if len(payload) != size:
                raise ValueError('corrupt retained LFS')
            continue
        payload = None
        if lfs:
            for candidate in (Path(lfs) / oid_lfs,
                              Path(lfs) / oid_lfs[:2] / oid_lfs[2:4] / oid_lfs):
                try:
                    payload = candidate.read_bytes()
                    break
                except OSError:
                    pass
        if payload is None or digest(payload) != oid_lfs or len(payload) != size:
            missing_lfs.append(oid_lfs)
            continue
        boundary('before_lfs')
        durable_put(root, key, payload)
        boundary('after_lfs')
    coverage = {'git': 'complete', 'lfs': 'incomplete' if missing_lfs else 'complete'}
    if wiki is not None:
        coverage['wiki'] = 'incomplete'
        if wiki is not False:
            child = wiki_manifest(root, repo_id, wiki)
            if all(v == 'complete' for v in child['coverage'].values()):
                coverage['wiki'] = 'complete'
    point_data = {'kind': 'point', 'identity': identity,
        'hash_algorithm': git(repo, 'rev-parse', '--show-object-format').decode().strip(),
        'refs': refs, 'head': head, 'packs': packs, 'objects': sorted(objects | known),
        'lfs': required, 'coverage': coverage,
        'parent_publication': chain[-1] if chain else None}
    if wiki is not None:
        point_data['wiki'] = wiki
    if chain:
        old_key = read(root, chain[-1], 'publication')['point']
        old = read(root, old_key, 'point')
        if dict(old, parent_publication=None) == dict(point_data, version=1, parent_publication=None):
            return old_key
    boundary('before_manifest')
    point = record(root, prefix + '/points', point_data)
    boundary('after_manifest')
    # Validate the entire inventory through archive bytes, never through staging.
    with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-', dir=root) as validation:
        validation_result = restore(root, point, Path(validation) / 'closure.git', allow_incomplete=True)
        if any(value == 'complete' and component not in validation_result['verified_components']
               for component, value in coverage.items()):
            raise ValueError('missing required component after publication staging')
    boundary('before_publication')
    record(root, prefix + '/commits', {'kind': 'publication', 'archive_id': archive['archive_id'],
           'repository_id': repo_id, 'point': point, 'parent': chain[-1] if chain else None,
           'writer': writer, 'operation_id': operation_id, 'recorded_at': timestamp()})
    boundary('after_publication')
    return point


def _restore_staged(root, point_key, destination, allow_incomplete=False):
    root, destination = Path(root), Path(destination)
    read(root, 'archive.json', 'archive')
    p = read(root, point_key, 'point')
    identity = read(root, p['identity'], 'identity')
    if identity['archive_id'] != read(root, 'archive.json', 'archive')['archive_id']:
        raise ValueError('archive identity mismatch')
    if p['hash_algorithm'] not in ('sha1', 'sha256'):
        raise ValueError('unsupported Git hash algorithm')
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination exists')
    coverage = p['coverage']
    if 'wiki' in p and 'wiki' not in coverage:
        raise ValueError('missing wiki coverage')
    if not all(coverage.get(c) == 'complete' for c in ('git', 'lfs')) or any(
            value != 'complete' for value in coverage.values()):
        if not allow_incomplete:
            raise ValueError('incomplete point requires explicit selection and --allow-incomplete')
    missing = []
    # Missing LFS can be recovered explicitly as partial data. Corruption always fails.
    for obj in p['packs']:
        if type(obj['size']) is not int or obj['size'] < 0 or len(read(root, obj['key'])) != obj['size']:
            raise ValueError('object size mismatch')
    for oid, obj in p['lfs'].items():
        if not re.fullmatch('[0-9a-f]{64}', oid) or obj['key'].split('/')[-1] != oid:
            raise ValueError('LFS identity mismatch')
        try:
            payload = read(root, obj['key'])
        except FileNotFoundError:
            if not allow_incomplete:
                raise
            missing.append(oid)
            continue
        if type(obj['size']) is not int or obj['size'] < 0 or len(payload) != obj['size']:
            raise ValueError('object size mismatch')
    destination.mkdir(parents=True, mode=0o700)
    git(destination, 'init', '--bare', '--template=', '--object-format='+p['hash_algorithm'])
    for pack in p['packs']:
        git(destination, 'index-pack', '--stdin', data=read(root, pack['key']))
    width = 40 if p['hash_algorithm'] == 'sha1' else 64
    for ref, oid in p['refs'].items():
        git(destination, 'check-ref-format', ref)
        if not ref.startswith('refs/') or not re.fullmatch('[0-9a-f]{'+str(width)+'}', oid):
            raise ValueError('invalid ref')
        git(destination, 'update-ref', ref, oid)
    if set(p['head']) == {'symbolic'}:
        git(destination, 'check-ref-format', p['head']['symbolic'])
        if not p['head']['symbolic'].startswith('refs/'):
            raise ValueError('invalid HEAD')
        git(destination, 'symbolic-ref', 'HEAD', p['head']['symbolic'])
    elif set(p['head']) == {'detached'} and re.fullmatch('[0-9a-f]{'+str(width)+'}', p['head']['detached']):
        git(destination, 'update-ref', '--no-deref', 'HEAD', p['head']['detached'])
    else:
        raise ValueError('invalid HEAD')
    actual_refs = dict(line.split(' ', 1) for line in git(
        destination, 'for-each-ref', '--format=%(refname) %(objectname)').decode().splitlines())
    if actual_refs != p['refs']:
        raise ValueError('restored refs mismatch')
    if 'detached' in p['head']:
        if git(destination, 'rev-parse', 'HEAD').decode().strip() != p['head']['detached']:
            raise ValueError('restored HEAD mismatch')
        git(destination, 'cat-file', '-e', p['head']['detached'] + '^{commit}')
    elif git(destination, 'symbolic-ref', 'HEAD').decode().strip() != p['head']['symbolic']:
        raise ValueError('restored HEAD mismatch')
    git(destination, 'fsck', '--full', '--strict')
    # fsck alone permits missing unreachable inventory. Validate every recorded object.
    width_pattern = '[0-9a-f]{'+str(width)+'}'
    if any(not re.fullmatch(width_pattern, oid) for oid in p['objects']):
        raise ValueError('invalid Git object identity')
    inventory_data = ('\n'.join(p['objects'])+'\n').encode() if p['objects'] else b''
    checked = git(destination, 'cat-file', '--batch-check', data=inventory_data)
    if b' missing\n' in checked:
        raise ValueError('missing Git inventory object')
    # Detect manifests that omit historical LFS requirements.
    tips = list(p['refs'].values()) + ([p['head']['detached']] if 'detached' in p['head'] else [])
    reachable = git(destination, 'rev-list', '--objects', '--no-object-names', '--stdin',
                    data=('\n'.join(tips)+'\n').encode()).decode().split() if tips else []
    requirements = required_lfs(destination, reachable)
    for lfs_oid, size in requirements.items():
        if lfs_oid not in p['lfs']:
            if not allow_incomplete or coverage.get('lfs') == 'complete':
                raise ValueError('manifest omits required LFS')
            missing.append(lfs_oid)
        elif p['lfs'][lfs_oid]['size'] != size:
            raise ValueError('LFS pointer size mismatch')
    for oid, obj in p['lfs'].items():
        if oid in missing:
            continue
        if oid != obj['key'].split('/')[-1]:
            raise ValueError('LFS identity mismatch')
        put(destination, f'lfs/objects/{oid[:2]}/{oid[2:4]}/{oid}', read(root, obj['key']))
    verified = ['git']
    if not missing and coverage.get('lfs') == 'complete':
        verified.append('lfs')
    wiki_result = None
    if p.get('wiki') is not None and p.get('wiki') is not False:
        wiki_manifest(root, identity['repository_id'], p['wiki'])
        wiki_result = _restore_staged(root, p['wiki'], destination / 'wiki.git', allow_incomplete)
        if wiki_result['complete'] and coverage.get('wiki') == 'complete':
            verified.append('wiki')
    elif coverage.get('wiki') == 'complete':
        raise ValueError('manifest omits required wiki')
    return {'git_integrity': 'passed', 'required_lfs': len(requirements), 'point': point_key,
            'missing_lfs': sorted(set(missing)), 'coverage': coverage,
            'verified_components': verified, 'wiki': wiki_result,
            'complete': not missing and set(coverage) <= set(verified)
                        and all(v == 'complete' for v in coverage.values())}


def install_directory(staged, destination):
    """Reserve a new destination only after validation. Never replace user data."""
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    try:
        os.rename(staged, destination)
    except BaseException:
        destination.rmdir()
        raise


def restore(root, point_key, destination, allow_incomplete=False, *, _pinned=False):
    if not _pinned:
        with security.destination_parent(destination) as pinned:
            return restore(root, point_key, pinned, allow_incomplete, _pinned=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination exists')
    with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-restore-',
                                     dir=destination.parent) as staging:
        repo = Path(staging) / 'repository.git'
        result = _restore_staged(root, point_key, repo, allow_incomplete)
        install_directory(repo, destination)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('archive'); init.add_argument('archive_id')
    cap = sub.add_parser('capture')
    for name in ('archive', 'repository', 'repository_id', 'source'):
        cap.add_argument(name)
    cap.add_argument('--lfs', help='Directory of flat or native Git LFS objects')
    wiki_options = cap.add_mutually_exclusive_group()
    wiki_options.add_argument('--wiki-point', help='Captured point under <repository_id>-wiki')
    wiki_options.add_argument('--wiki-unavailable', action='store_true',
                              help='Enabled wiki is unavailable; retain incomplete coverage')
    cap.add_argument('--operation-id', help='Stable identity for replay of this capture')
    res = sub.add_parser('restore')
    for name in ('archive', 'point', 'destination'):
        res.add_argument(name)
    own = sub.add_parser('ownership')
    own.add_argument('archive')
    take = sub.add_parser('takeover')
    take.add_argument('archive')
    take.add_argument('--previous-owner', required=True, help='Ownership key, or none for a legacy archive')
    take.add_argument('--fencing-evidence', required=True, help='Nonsecret record of external fencing')
    take.add_argument('--heads-file', required=True, help='JSON heads from ownership inspection')
    args = parser.parse_args()
    if args.command == 'init':
        initialize(args.archive, args.archive_id)
    elif args.command == 'capture':
        print(capture(args.archive, args.repository, args.repository_id, args.source, args.lfs, args.operation_id,
                      wiki=False if args.wiki_unavailable else args.wiki_point))
    elif args.command == 'restore':
        print(json.dumps(restore(args.archive, args.point, args.destination)))
    else:
        from gitpickle_writer import Writer
        with Writer(args.archive) as writer:
            if args.command == 'takeover':
                previous = None if args.previous_owner == 'none' else args.previous_owner
                writer.takeover(previous, args.fencing_evidence, json.loads(Path(args.heads_file).read_text()))
            print(json.dumps({'owner': writer.owner_key, 'writable': writer.writable,
                              'heads': writer.reconcile()}))


if __name__ == '__main__':
    main()
