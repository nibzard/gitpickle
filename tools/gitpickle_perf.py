#!/usr/bin/env python3
"""Versioned, deterministic native Git/local storage benchmark harness."""
import argparse
import contextlib
import hashlib
import html
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
VERSION = 1
SEED = 20261015
CATALOG = ('empty-and-tiny', 'git-100m', 'git-1g', 'git-10g', 'lfs-2g',
           'portfolio-404', 'history-rewritten', 'failure-matrix')
SCENARIOS = ('initial-capture', 'no-change', 'small-change', 'heavy-change',
             'warm-clone', 'cold-clone', 'verified-restore', 'index-rebuild', 'process-restart')
FAULTS = ('interrupted-upload', 'lost-response', 'throttling', 'credential-loss', 'corrupt-object')
SCHEMA = json.loads((ROOT / 'perf/result-schema-v1.json').read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(canonical(value) + b'\n')
    temporary.replace(path)


def git_env():
    # Do not inherit host Git configuration, repository selection, or identity.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_AUTHOR_NAME='Fixture',
               GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_NAME='Fixture',
               GIT_COMMITTER_EMAIL='fixture@example.invalid',
               GIT_AUTHOR_DATE='2000-01-01T00:00:00Z', GIT_COMMITTER_DATE='2000-01-01T00:00:00Z',
               LC_ALL='C', TZ='UTC')
    return env


def git(repo, *args, data=None):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'gc.auto=0',
                           '-C', str(repo), *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=git_env(), check=True).stdout


def disk_bytes(path):
    total = 0
    for item in Path(path).rglob('*'):
        try:
            if item.is_file():
                total += item.stat().st_size
        except FileNotFoundError:
            pass  # A child can rename or remove staging files during a sample.
    return total


def refs(repo):
    return git(repo, 'for-each-ref', '--format=%(refname) %(objectname)').decode().splitlines()


def payload(seed, length, mixed=False):
    """Counter-mode SHA-256 blocks; bounded memory and independent of Python PRNG versions."""
    counter = 0
    prefix = str(seed).encode() + b':'
    while length:
        size = min(length, 1024 * 1024)
        block = bytearray()
        while len(block) < size:
            block.extend(hashlib.sha256(prefix + counter.to_bytes(8, 'big')).digest())
            counter += 1
        if mixed:
            block[:size // 2] = b'\0' * (size // 2)
        yield bytes(block[:size])
        length -= size


def make_repo(path, seed, commits, logical_bytes, mixed=False):
    path.mkdir(parents=True)
    git(path, 'init', '--bare', '--object-format=sha1', '--initial-branch=main', '--template=')
    if commits:
        proc = subprocess.Popen(['git', '-C', str(path), 'fast-import', '--quiet'],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, env=git_env())
        try:
            for index in range(commits):
                size = logical_bytes // commits + (index < logical_bytes % commits)
                proc.stdin.write(f'blob\nmark :{2*index+1}\ndata {size}\n'.encode())
                for chunk in payload(f'{seed}/{index}', size, mixed):
                    proc.stdin.write(chunk)
                proc.stdin.write((f'\ncommit refs/heads/main\nmark :{2*index+2}\n'
                                  f'committer Fixture <fixture@example.invalid> {946684800+index} +0000\n'
                                  f'data 8\nfixture\nM 100644 :{2*index+1} file-{index:06d}\n\n').encode())
            proc.stdin.write(b'done\n')
            proc.stdin.close()
            error = proc.stderr.read()
            if proc.wait() != 0:
                raise RuntimeError(error.decode())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stderr.close()
        git(path, 'update-ref', 'refs/heads/topic', 'main')
        git(path, 'tag', '-a', 'v1', '-m', 'fixture', 'main')
        git(path, 'repack', '-ad', '--threads=1')
    return measure_repo(path, logical_bytes)


def measure_repo(path, logical_bytes):
    git(path, 'fsck', '--full', '--strict')
    listing = git(path, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname) %(objecttype) %(objectsize)')
    counts = {}
    measured_logical_bytes = 0
    for line in listing.decode().splitlines():
        kind = line.split()[1]
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 'blob':
            measured_logical_bytes += int(line.split()[2])
    packed = sum(p.stat().st_size for p in (path / 'objects/pack').glob('*.pack'))
    return dict(name=path.name, refs=refs(path), head=git(path, 'symbolic-ref', 'HEAD').decode().strip(),
                object_counts=counts, object_digest=hashlib.sha256(listing).hexdigest(),
                packed_bytes=packed, generated_content_bytes=logical_bytes,
                logical_bytes=measured_logical_bytes, ref_count=len(refs(path)),
                object_count=sum(counts.values()),
                packed_to_logical_ratio=packed / measured_logical_bytes if measured_logical_bytes else None,
                pack_hashes={p.name: digest(p) for p in sorted((path / 'objects/pack').glob('*.pack'))})


def add_lfs(repo, base, seed, size):
    records = []
    for index in range(8):
        pending = base / f'lfs-{index}.tmp'
        with pending.open('wb') as stream:
            for chunk in payload(f'{seed}/lfs/{index}', size):
                stream.write(chunk)
        oid = digest(pending)
        target = base / 'lfs' / oid
        target.parent.mkdir(exist_ok=True)
        pending.rename(target)
        records.append(dict(oid=oid, size=size, path=f'lfs/{oid}'))
    tree = []
    for index, record in enumerate(records):
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{record["oid"]}\nsize {size}\n'.encode()
        oid = git(repo, 'hash-object', '-w', '--stdin', data=pointer).decode().strip()
        tree.append(f'100644 blob {oid}\tasset-{index}.bin\n')
    attr = git(repo, 'hash-object', '-w', '--stdin', data=b'*.bin filter=lfs diff=lfs merge=lfs -text\n').decode().strip()
    tree.append(f'100644 blob {attr}\t.gitattributes\n')
    tree_oid = git(repo, 'mktree', data=''.join(tree).encode()).decode().strip()
    commit = git(repo, 'commit-tree', tree_oid, '-m', 'LFS fixture').decode().strip()
    git(repo, 'update-ref', 'refs/heads/main', commit)
    git(repo, 'repack', '-ad', '--threads=1')
    return records


def generate(name, destination, seed=SEED, profile='full'):
    destination = Path(destination)
    if destination.exists():
        raise ValueError(f'Fixture destination already exists: {destination}')
    destination.mkdir(parents=True)
    manifest = dict(schema_version=VERSION, fixture_version=VERSION, fixture=name,
                    seed=seed, profile=profile, git_version=git(ROOT, '--version').decode().strip(),
                    generator='sha256-counter-v1', repositories=[], lfs=[], historical=[], faults=[])
    def add(label, commits, size, mixed=False):
        path = destination / label
        record = make_repo(path, f'{seed}/{label}', commits, size, mixed)
        manifest['repositories'].append(record)
        return path
    if name == 'empty-and-tiny':
        add('empty.git', 0, 0)
        add('tiny.git', 3, 2048)
    elif name in ('git-100m', 'git-1g', 'git-10g'):
        size, commits, mixed = {'git-100m': (100*2**20, 1000, False),
                                'git-1g': (2*2**30, 2000, True),
                                'git-10g': (10*2**30, 10000, False)}[name]
        if profile == 'smoke':
            size, commits = 128*1024, 10
        repo = add('source.git', commits, size, mixed)
        if name == 'git-10g':
            for index in range(1000):
                git(repo, 'update-ref', f'refs/heads/branch-{index:04d}', 'main')
            manifest['repositories'][-1] = measure_repo(repo, size)
    elif name == 'lfs-2g':
        repo = add('source.git', 3, 2048)
        manifest['lfs'] = add_lfs(repo, destination, seed, 256*2**20 if profile == 'full' else 4096)
        manifest['repositories'][-1] = measure_repo(repo, 2048)
    elif name == 'portfolio-404':
        for index in range(404):
            add(f'repo-{index:03d}.git', 3 if index < 400 else 30,
                2048 if index < 400 else (index-399)*2**20)
    elif name == 'history-rewritten':
        repo = add('source.git', 6, 8192)
        git(repo, 'bundle', 'create', str(destination / 'before.bundle'), '--all')
        manifest['historical'] = [dict(path='before.bundle', sha256=digest(destination / 'before.bundle'),
                                       refs=refs(repo))]
        tree = git(repo, 'rev-parse', 'main^{tree}').decode().strip()
        rewritten = git(repo, 'commit-tree', tree, '-m', 'replacement root').decode().strip()
        git(repo, 'update-ref', 'refs/heads/main', rewritten)
        git(repo, 'update-ref', '-d', 'refs/heads/topic')
        git(repo, 'update-ref', '-d', 'refs/tags/v1')
        git(repo, 'repack', '-ad', '--threads=1')
        manifest['repositories'][-1] = measure_repo(repo, 8192)
    elif name == 'failure-matrix':
        add('source.git', 3, 2048)
        manifest['faults'] = list(FAULTS)
    else:
        raise ValueError(f'Unknown fixture: {name}')
    manifest['lfs_bytes'] = sum(x['size'] for x in manifest['lfs'])
    manifest['fixture_digest'] = hashlib.sha256(canonical(manifest)).hexdigest()
    write_json(destination / 'manifest.json', manifest)
    return manifest


def validate_fixture(path):
    manifest = json.loads((path / 'manifest.json').read_text())
    unsigned = {k: v for k, v in manifest.items() if k != 'fixture_digest'}
    if manifest['fixture_digest'] != hashlib.sha256(canonical(unsigned)).hexdigest():
        raise ValueError('Invalid fixture manifest digest')
    if manifest['fixture_version'] != VERSION or manifest['schema_version'] != VERSION:
        raise ValueError('Unsupported fixture version')
    for record in manifest['repositories']:
        if measure_repo(path / record['name'], record['generated_content_bytes']) != record:
            raise ValueError(f'Fixture changed: {record["name"]}')
    for record in manifest['lfs'] + manifest['historical']:
        if digest(path / record['path']) != record.get('oid', record.get('sha256')):
            raise ValueError('Fixture payload digest mismatch')
    return manifest


class Storage:
    """Instrumented local emulator. Events count attempted operations and actual bytes."""
    def __init__(self, path):
        self.path = path
        path.mkdir()
        self.events = []
        self.fault = None

    def request(self, operation, key='', data=None, retry=False):
        event = dict(operation=operation, key=key, uploaded_bytes=0, downloaded_bytes=0,
                     outcome='ok', retry=retry)
        self.events.append(event)
        fault, self.fault = self.fault, None
        try:
            if fault in ('throttling', 'credential-loss'):
                raise OSError(fault)
            target = self.path / key
            if operation == 'PUT':
                if fault == 'interrupted-upload':
                    event['uploaded_bytes'] = len(data) // 2
                    raise OSError(fault)
                target.write_bytes(data)
                event['uploaded_bytes'] = len(data)
                if fault == 'lost-response':
                    raise OSError(fault)
                if fault == 'corrupt-object':
                    target.write_bytes(b'corrupt')
            elif operation == 'GET':
                value = target.read_bytes()
                event['downloaded_bytes'] = len(value)
                return value
            elif operation == 'HEAD':
                return target.exists()
            elif operation == 'LIST':
                return sorted(p.name for p in self.path.iterdir())
            else:
                raise ValueError('Unknown storage operation')
        except OSError:
            event['outcome'] = 'error'
            raise
        return None

    def put_verified(self, key, data):
        for attempt in range(3):
            try:
                self.request('PUT', key, data, retry=attempt > 0)
                break
            except OSError as error:
                if str(error) == 'credential-loss':
                    raise
                if attempt == 2:
                    raise
        if hashlib.sha256(self.request('GET', key)).hexdigest() != hashlib.sha256(data).hexdigest():
            raise ValueError('Storage corruption detected')

    def transfer_file(self, operation, key, local):
        """Stream large payloads while recording bytes actually copied."""
        event = dict(operation=operation, key=key, uploaded_bytes=0,
                     downloaded_bytes=0, outcome='ok', retry=False)
        self.events.append(event)
        source, target = (local, self.path / key) if operation == 'PUT' else (self.path / key, local)
        metric = 'uploaded_bytes' if operation == 'PUT' else 'downloaded_bytes'
        try:
            with source.open('rb') as reader, target.open('wb') as writer:
                for chunk in iter(lambda: reader.read(1024*1024), b''):
                    writer.write(chunk)
                    event[metric] += len(chunk)
        except OSError:
            event['outcome'] = 'error'
            raise
        if digest(target) != key:
            event['outcome'] = 'corrupt'
            raise ValueError('Transferred payload digest mismatch')


class Monitor:
    """Sample this harness and descendants on Linux; CPU includes reaped children."""
    def __init__(self, directory):
        self.directory = directory
        self.stop = threading.Event()
        self.peak = 0
        self.disk = 0
        self.self_peak = 0
        self.samples = 0
        self.error = None

    def sample(self):
        processes = {}
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                    processes[int(entry.name)] = (int(fields[1]), int(fields[21])*os.sysconf('SC_PAGE_SIZE'))
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
        family = {os.getpid()}
        while True:
            expanded = family | {pid for pid, (parent, _) in processes.items() if parent in family}
            if expanded == family:
                break
            family = expanded
        self.peak = max(self.peak, sum(processes.get(pid, (0, 0))[1] for pid in family))
        self.self_peak = max(self.self_peak, processes.get(os.getpid(), (0, 0))[1])
        self.disk = max(self.disk, disk_bytes(self.directory))
        self.samples += 1

    def loop(self):
        try:
            while not self.stop.wait(.02):
                self.sample()
        except Exception as error:
            self.error = str(error)

    def __enter__(self):
        self.sample()
        self.cpu_start = cpu_seconds()
        self.thread = threading.Thread(target=self.loop)
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join()
        self.sample()
        self.cpu = cpu_seconds() - self.cpu_start
        if self.error:
            raise RuntimeError(f'Resource monitor failed: {self.error}')


def cpu_seconds():
    return sum(r.ru_utime + r.ru_stime for r in (resource.getrusage(resource.RUSAGE_SELF),
                                                resource.getrusage(resource.RUSAGE_CHILDREN)))


@contextlib.contextmanager
def stage(stages, name):
    start = time.perf_counter_ns()
    try:
        yield
    finally:
        stages[name] = (time.perf_counter_ns() - start) / 1e6


def capture(source, manifest, storage, scratch, stages):
    index = []
    with stage(stages, 'source_observation'):
        observed = [(record, refs(source / record['name'])) for record in manifest['repositories']]
    with stage(stages, 'object_validation'):
        for record, _ in observed:
            git(source / record['name'], 'fsck', '--full', '--strict')
    packs = []
    with stage(stages, 'pack_generation'):
        for record, current_refs in observed:
            item = dict(name=record['name'], refs=current_refs, head=record['head'], bundle=None)
            if current_refs:
                bundle = scratch / (record['name'] + '.bundle')
                git(source / record['name'], 'bundle', 'create', str(bundle), '--all')
                item['bundle'] = digest(bundle)
                packs.append((item['bundle'], bundle))
            index.append(item)
        for record in manifest['historical']:
            packs.append((record['sha256'], source / record['path']))
        for record in manifest['lfs']:
            packs.append((record['oid'], source / record['path']))
    with stage(stages, 'upload'):
        for key, path in packs:
            if not storage.request('HEAD', key):
                storage.transfer_file('PUT', key, path)
    publication = dict(repositories=index, lfs=manifest['lfs'], historical=manifest['historical'])
    with stage(stages, 'publication'):
        storage.put_verified('index.json', canonical(publication))
    with stage(stages, 'independent_verification'):
        for key, _ in packs:
            check = scratch / 'readback.tmp'
            storage.transfer_file('GET', key, check)
            check.unlink()
    return publication


def restore(storage, destination, stages):
    with stage(stages, 'inventory_rebuild'):
        keys = storage.request('LIST')
        if 'index.json' not in keys:
            raise ValueError('Missing publication')
        index = json.loads(storage.request('GET', 'index.json'))
    verified_refs = 0
    with stage(stages, 'download_and_reconstruction'):
        for record in index['repositories']:
            repo = destination / record['name']
            repo.mkdir()
            git(repo, 'init', '--bare', '--initial-branch=main', '--template=')
            if record['bundle']:
                bundle = destination / 'restore.bundle'
                storage.transfer_file('GET', record['bundle'], bundle)
                git(repo, 'fetch', str(bundle), '+refs/*:refs/*')
                bundle.unlink()
            git(repo, 'symbolic-ref', 'HEAD', record['head'])
    with stage(stages, 'final_integrity_validation'):
        for record in index['repositories']:
            repo = destination / record['name']
            git(repo, 'fsck', '--full', '--strict')
            if refs(repo) != record['refs'] or git(repo, 'symbolic-ref', 'HEAD').decode().strip() != record['head']:
                raise ValueError('Restored refs or HEAD differ')
            verified_refs += len(record['refs'])
        for record in index['lfs'] + index['historical']:
            key = record.get('oid', record.get('sha256'))
            restored_payload = destination / key
            storage.transfer_file('GET', key, restored_payload)
            if 'oid' in record and restored_payload.stat().st_size != record['size']:
                raise ValueError('LFS size mismatch')
            if 'refs' in record:
                recovered = git(destination, 'bundle', 'list-heads', str(restored_payload)).decode().splitlines()
                recovered = sorted(' '.join(line.split()[::-1]) for line in recovered if not line.endswith(' HEAD'))
                if recovered != record['refs']:
                    raise ValueError('Historical refs differ')
                historical_repo = destination / 'historical.git'
                git(destination, 'clone', '--mirror', str(restored_payload), str(historical_repo))
                git(historical_repo, 'fsck', '--full', '--strict')
    return dict(outcome='passed', verified_refs=verified_refs, verified_lfs=len(index['lfs']),
                missing_dependencies=0, lost_acknowledged_updates=0)


def mutate(repo, heavy, logical_bytes):
    tree = git(repo, 'rev-parse', 'main^{tree}').decode().strip()
    parent = git(repo, 'rev-parse', 'main').decode().strip()
    size = max(4096, logical_bytes // 4) if heavy else 64
    content = repo / 'change.tmp'
    with content.open('wb') as stream:
        for chunk in payload('heavy-change' if heavy else 'small-change', size):
            stream.write(chunk)
    blob = git(repo, 'hash-object', '-w', str(content)).decode().strip()
    content.unlink()
    entries = git(repo, 'ls-tree', tree) + f'100644 blob {blob}\tchange.txt\n'.encode()
    new_tree = git(repo, 'mktree', data=entries).decode().strip()
    commit = git(repo, 'commit-tree', new_tree, '-p', parent, '-m', 'benchmark change').decode().strip()
    git(repo, 'update-ref', 'refs/heads/main', commit)
    return size


def sample(source, manifest, scenario, work):
    stages = {}
    storage = Storage(work / 'storage')
    scratch = work / 'staging'
    scratch.mkdir()
    destination = work / 'restored'
    destination.mkdir()
    # Establish equivalent initial state outside the measured region.
    active_source = source
    logical_new_bytes = sum(r['logical_bytes'] for r in manifest['repositories']) + manifest['lfs_bytes'] if scenario == 'initial-capture' else 0
    changed_repositories = len(manifest['repositories']) if scenario == 'initial-capture' else 0
    if scenario != 'initial-capture':
        capture(source, manifest, storage, scratch, {})
    if scenario in ('small-change', 'heavy-change'):
        active_source = work / 'changed'
        shutil.copytree(source, active_source)
        for record in manifest['repositories']:
            if record['refs']:
                logical_new_bytes += mutate(active_source / record['name'], scenario == 'heavy-change', record['logical_bytes'])
                changed_repositories += 1
    storage.events.clear()
    start = time.perf_counter_ns()
    with Monitor(work) as monitor:
        if scenario in ('initial-capture', 'no-change', 'small-change', 'heavy-change'):
            capture(active_source, manifest, storage, scratch, stages)
            correctness = restore(storage, destination, stages)
        elif scenario in ('warm-clone', 'cold-clone'):
            with stage(stages, 'native_git_clone'):
                for record in manifest['repositories']:
                    git(work, 'clone', '--mirror', '--no-local', str(source / record['name']),
                        str(destination / record['name']))
            with stage(stages, 'final_integrity_validation'):
                for record in manifest['repositories']:
                    git(destination / record['name'], 'fsck', '--full', '--strict')
                    if refs(destination / record['name']) != record['refs']:
                        raise ValueError('Clone refs differ')
            correctness = dict(outcome='passed', verified_refs=sum(len(r['refs']) for r in manifest['repositories']),
                               verified_lfs=0, missing_dependencies=0, lost_acknowledged_updates=0)
        elif scenario == 'process-restart':
            with stage(stages, 'process_restart_and_inventory'):
                subprocess.run([sys.executable, str(Path(__file__).resolve()), '_inventory',
                                str(storage.path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            correctness = restore(storage, destination, stages)
        else:
            correctness = restore(storage, destination, stages)
    payload_bytes = sum(e['uploaded_bytes'] for e in storage.events if e['key'] != 'index.json')
    if scenario == 'no-change' and payload_bytes:
        raise ValueError('Unchanged input uploaded payload')
    return dict(elapsed_ms=(time.perf_counter_ns()-start)/1e6, stages_ms=stages,
                logical_new_bytes=logical_new_bytes, changed_repositories=changed_repositories,
                upload_amplification=payload_bytes/logical_new_bytes if logical_new_bytes else None,
                process_tree_peak_rss_bytes=monitor.peak, harness_peak_rss_bytes=monitor.self_peak,
                cpu_seconds=monitor.cpu, staging_cache_peak_disk_bytes=monitor.disk,
                resource_samples=monitor.samples, resource_interval_ms=20,
                storage_requests={op: sum(e['operation'] == op for e in storage.events) for op in ('PUT','GET','HEAD','LIST')},
                native_git_pack_received_bytes=sum(p.stat().st_size for p in destination.rglob('*.pack')) if scenario in ('warm-clone', 'cold-clone') else 0,
                retries=sum(e['retry'] for e in storage.events),
                uploaded_bytes=sum(e['uploaded_bytes'] for e in storage.events),
                downloaded_bytes=sum(e['downloaded_bytes'] for e in storage.events),
                git_lfs_payload_uploaded_bytes=payload_bytes, retained_bytes=disk_bytes(storage.path),
                storage_events=storage.events, correctness=correctness)


def fault_sample(fault, work):
    storage = Storage(work / 'storage')
    storage.fault = fault
    data = b'deterministic fault payload'
    key = hashlib.sha256(data).hexdigest()
    error = None
    start = time.perf_counter_ns()
    with Monitor(work) as monitor:
        try:
            storage.put_verified(key, data)
        except (OSError, ValueError) as failure:
            error = str(failure)
    expected_failure = fault in ('credential-loss', 'corrupt-object')
    if expected_failure != bool(error):
        raise ValueError('Fault outcome violates contract')
    if not expected_failure and storage.request('GET', key) != data:
        raise ValueError('Retry did not recover data')
    return dict(elapsed_ms=(time.perf_counter_ns()-start)/1e6, stages_ms={'fault_execution': (time.perf_counter_ns()-start)/1e6},
                logical_new_bytes=len(data), changed_repositories=0,
                upload_amplification=sum(e['uploaded_bytes'] for e in storage.events)/len(data),
                process_tree_peak_rss_bytes=monitor.peak, harness_peak_rss_bytes=monitor.self_peak,
                staging_cache_peak_disk_bytes=monitor.disk, cpu_seconds=monitor.cpu,
                resource_samples=monitor.samples, resource_interval_ms=20,
                storage_requests={op: sum(e['operation'] == op for e in storage.events) for op in ('PUT','GET','HEAD','LIST')},
                native_git_pack_received_bytes=0,
                retries=sum(e['retry'] for e in storage.events), storage_events=storage.events,
                uploaded_bytes=sum(e['uploaded_bytes'] for e in storage.events),
                downloaded_bytes=sum(e['downloaded_bytes'] for e in storage.events),
                git_lfs_payload_uploaded_bytes=sum(e['uploaded_bytes'] for e in storage.events),
                retained_bytes=disk_bytes(storage.path), expected_error=error,
                correctness=dict(outcome='passed', verified_refs=0, verified_lfs=0,
                                 missing_dependencies=0, lost_acknowledged_updates=0))


def environment():
    def version(command):
        return subprocess.check_output(command, env=git_env(), text=True).strip()
    value = dict(architecture=platform.machine(), kernel=platform.release(), os=platform.platform(),
                 python=platform.python_version(), git=version(['git', '--version']),
                 git_lfs=version(['git', 'lfs', 'version']), rustc=version(['rustc', '--version']),
                 cpu=Path('/proc/cpuinfo').read_text().split('model name', 1)[-1].splitlines()[0].strip(': \t'),
                 cpu_count=os.cpu_count(), memory=Path('/proc/meminfo').read_text().splitlines()[0],
                 filesystem=version(['stat', '-f', '-c', '%T', str(ROOT)]), build_profile='python-native-git-reference',
                 storage_mode='local-emulator', transport='local-files', bandwidth=None, rtt=None,
                 tls='not-applicable', region='not-applicable', endpoint='local-files', concurrency=1,
                 resource_method='Linux /proc tree RSS sampled every 20ms; CPU includes reaped descendants; disk logical bytes')
    return value, hashlib.sha256(canonical(value)).hexdigest()


def summarize(values):
    median = statistics.median(values)
    rng = random.Random(SEED)
    boot = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(1000))
    return dict(elapsed_ms_median=median, elapsed_ms_p95=sorted(values)[math.ceil(.95*len(values))-1] if len(values) >= 30 else None,
                median_ci95_ms=[boot[24], boot[974]], uncertainty='bootstrap percentile interval; small samples are exploratory' if len(values) < 30 else 'bootstrap percentile interval')


def result_path(label):
    path = Path(label)
    if path.suffix == '.json':
        return path
    if path.name != label or label in ('.', '..'):
        raise ValueError('Use a result label or a JSON path')
    return ROOT / 'perf/results' / f'{label}.json'


def validate_result(result):
    def require(value, contract):
        if not isinstance(value, dict) or any(key not in value for key in contract['required']):
            raise ValueError('Missing required evidence fields')
    require(result, SCHEMA)
    require(result['environment'], SCHEMA['properties']['environment'])
    if result['runner_fingerprint'] != hashlib.sha256(canonical(result['environment'])).hexdigest():
        raise ValueError('Runner fingerprint mismatch')
    if result['scope'] != 'native-git-local-storage-reference':
        raise ValueError('Unsupported benchmark scope')
    if result['schema_version'] != VERSION or result['record_kind'] != 'measured':
        raise ValueError('Unsupported or unmeasured result')
    if result['status'] != 'complete' or not result['rows']:
        raise ValueError('Incomplete run')
    if not isinstance(result['repetitions'], int) or result['repetitions'] < 1:
        raise ValueError('Invalid repetition count')
    expected = result['expected_scenarios']
    if sorted(row['scenario'] for row in result['rows']) != sorted(expected) or len(set(expected)) != len(expected):
        raise ValueError('Missing or duplicate scenarios')
    for row in result['rows']:
        require(row, SCHEMA['$defs']['row'])
        manifest = row['fixture_manifest']
        require(manifest, SCHEMA['$defs']['row']['properties']['fixture_manifest'])
        unsigned = {key: value for key, value in manifest.items() if key != 'fixture_digest'}
        if (row['fixture_version'] != VERSION or row['scenario_version'] != VERSION
                or manifest['fixture_version'] != VERSION
                or row['fixture_digest'] != manifest['fixture_digest']
                or row['fixture_digest'] != hashlib.sha256(canonical(unsigned)).hexdigest()):
            raise ValueError('Invalid embedded fixture evidence')
        if len(row['raw_samples']) != result['repetitions'] or not row['raw_samples']:
            raise ValueError('Missing samples')
        if row['correctness'] != 'passed':
            raise ValueError('Failed correctness')
        for sample_value in row['raw_samples']:
            require(sample_value, SCHEMA['$defs']['sample'])
            require(sample_value['correctness'], SCHEMA['$defs']['sample']['properties']['correctness'])
            if (sample_value['correctness']['outcome'] != 'passed'
                    or sample_value['correctness']['lost_acknowledged_updates']
                    or sample_value['correctness']['missing_dependencies']):
                raise ValueError('Failed sample correctness')
            for key in ('elapsed_ms', 'process_tree_peak_rss_bytes', 'cpu_seconds', 'staging_cache_peak_disk_bytes',
                        'uploaded_bytes', 'downloaded_bytes', 'resource_samples', 'retained_bytes',
                        'native_git_pack_received_bytes', 'git_lfs_payload_uploaded_bytes', 'retries',
                        'harness_peak_rss_bytes', 'logical_new_bytes', 'changed_repositories'):
                value = sample_value[key]
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f'Invalid metric: {key}')
            if not sample_value['resource_samples'] or not sample_value['stages_ms']:
                raise ValueError('Missing resource or stage measurements')
            if any(not math.isfinite(v) or v < 0 for v in sample_value['stages_ms'].values()):
                raise ValueError('Invalid stage measurement')
            for metric in ('uploaded_bytes', 'downloaded_bytes'):
                if sample_value[metric] != sum(event[metric] for event in sample_value['storage_events']):
                    raise ValueError('Byte counts differ from trace')
            if sample_value['retries'] != sum(event['retry'] for event in sample_value['storage_events']):
                raise ValueError('Retry count differs from trace')
            for op in ('PUT', 'GET', 'HEAD', 'LIST'):
                if sample_value['storage_requests'][op] != sum(e['operation'] == op for e in sample_value['storage_events']):
                    raise ValueError('Request counts differ from trace')
            if row['scenario'].endswith('/no-change') and sample_value['git_lfs_payload_uploaded_bytes'] != 0:
                raise ValueError('No-change payload upload')
        if row['metrics'] != summarize([x['elapsed_ms'] for x in row['raw_samples']]):
            raise ValueError('Summary differs from raw samples')


def compare(candidate, baseline):
    validate_result(candidate)
    validate_result(baseline)
    if candidate['runner_fingerprint'] != baseline['runner_fingerprint']:
        raise ValueError('Incompatible runners')
    rows = {r['scenario']: r for r in baseline['rows']}
    comparisons = []
    for row in candidate['rows']:
        old = rows.get(row['scenario'])
        if old is None or any(row[k] != old[k] for k in ('fixture_digest', 'fixture_version', 'scenario_version', 'cache_state')):
            raise ValueError('Incompatible fixture, cache state, or scenario')
        previous = old['metrics']['elapsed_ms_median']
        current = row['metrics']['elapsed_ms_median']
        delta = current / previous - 1 if previous else 0
        confirmed = row['metrics']['median_ci95_ms'][0] > old['metrics']['median_ci95_ms'][1] * 1.10
        gate = 'block' if delta > .10 and current-previous > 10 and confirmed and min(candidate['repetitions'], baseline['repetitions']) >= 30 else ('warn' if delta > .05 else 'pass')
        request_delta = {op: statistics.median(s['storage_requests'][op] for s in row['raw_samples']) - statistics.median(s['storage_requests'][op] for s in old['raw_samples']) for op in ('PUT','GET','HEAD','LIST')}
        resource_delta = {}
        for metric, floor in (('process_tree_peak_rss_bytes', 1024*1024),
                              ('staging_cache_peak_disk_bytes', 1024*1024), ('cpu_seconds', .01)):
            current_resource = statistics.median(s[metric] for s in row['raw_samples'])
            old_resource = statistics.median(s[metric] for s in old['raw_samples'])
            ratio = current_resource / old_resource - 1 if old_resource else None
            resource_delta[metric] = dict(absolute=current_resource-old_resource, fraction=ratio, noise_floor=floor)
            if ratio is not None and ratio > .05 and current_resource-old_resource > floor and gate == 'pass':
                gate = 'warn'
            if (ratio is not None and ratio > .10 and current_resource-old_resource > floor
                    and min(candidate['repetitions'], baseline['repetitions']) >= 30):
                current_interval = summarize([s[metric] for s in row['raw_samples']])['median_ci95_ms']
                old_interval = summarize([s[metric] for s in old['raw_samples']])['median_ci95_ms']
                resource_delta[metric]['candidate_ci95'] = current_interval
                resource_delta[metric]['baseline_ci95'] = old_interval
                if current_interval[0] > old_interval[1] * 1.10:
                    gate = 'block'
        if any(request_delta.values()) and gate == 'pass':
            gate = 'warn'
        if gate == 'pass' and min(candidate['repetitions'], baseline['repetitions']) < 30:
            gate = 'exploratory'
        comparisons.append(dict(scenario=row['scenario'], baseline_ms=previous, candidate_ms=current,
                                delta_percent=100*delta, gate=gate, storage_request_delta=request_delta,
                                resource_delta=resource_delta))
    return comparisons


def report(result, output, baseline=None):
    validate_result(result)
    comparisons = compare(result, baseline) if baseline else []
    lookup = {r['scenario']: r for r in comparisons}
    lines = ['<!doctype html><meta charset="utf-8"><title>GitPickle benchmark</title>',
             '<h1>Native Git and local storage reference</h1>',
             '<p>This report does not establish application or provider acceptance.</p>',
             f'<p>Commit: {html.escape(result["commit"])}; runner: {result["runner_fingerprint"]}</p>',
             '<table><tr><th>Scenario</th><th>Median ms</th><th>95% interval</th><th>Comparison</th><th>Correctness</th></tr>']
    for row in result['rows']:
        gate = lookup.get(row['scenario'], {'gate': 'not_run'})
        lines.append('<tr>' + ''.join(f'<td>{html.escape(str(value))}</td>' for value in
                     (row['scenario'], round(row['metrics']['elapsed_ms_median'], 3), row['metrics']['median_ci95_ms'], gate, row['correctness'])) + '</tr>')
    lines += ['</table>', f'<p><a href="{html.escape(Path(output).with_suffix(".json").name)}">Raw samples and environment</a></p>']
    Path(output).write_text('\n'.join(lines))
    return comparisons


def bench(args):
    if args.repetitions < 1:
        raise ValueError('Repetitions must be positive')
    names = ['empty-and-tiny', 'history-rewritten', 'failure-matrix'] if args.suite == 'quick' else ([*CATALOG] if args.suite == 'all' else [args.suite])
    selected = args.scenarios.split(',') if args.scenarios else list(SCENARIOS)
    if len(selected) != len(set(selected)) or any(s not in SCENARIOS for s in selected):
        raise ValueError('Unknown or duplicate scenario')
    env, fingerprint = environment()
    output = result_path(args.output)
    if output.exists():
        raise ValueError(f'Result already exists: {output}')
    result = dict(schema_version=VERSION, record_kind='measured', status='incomplete',
                  scope='native-git-local-storage-reference', commit=git(ROOT, 'rev-parse', 'HEAD').decode().strip(),
                  source_dirty=bool(git(ROOT, 'status', '--porcelain', '--untracked-files=no')),
                  harness_sha256=digest(Path(__file__)),
                  environment=env, runner_fingerprint=fingerprint, repetitions=args.repetitions,
                  suite=args.suite, rows=[], expected_scenarios=[f'{name}/{scenario}' for name in names for scenario in (FAULTS if name == 'failure-matrix' else selected)], comparison='not_run')
    write_json(output, result)
    prefix = os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')
    work_root = ROOT / 'fixtures/generated'
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=prefix+'-', dir=work_root) as temp:
            base = Path(temp)
            for name in names:
                source = base / name
                manifest = generate(name, source, profile=args.profile)
                validate_fixture(source)
                for scenario in (FAULTS if name == 'failure-matrix' else selected):
                    raw = []
                    for _ in range(args.repetitions):
                        with tempfile.TemporaryDirectory(prefix='sample-', dir=base) as scratch:
                            raw.append(fault_sample(scenario, Path(scratch)) if name == 'failure-matrix' else sample(source, manifest, scenario, Path(scratch)))
                    result['rows'].append(dict(scenario=f'{name}/{scenario}', scenario_version=VERSION, fixture_version=VERSION,
                                               fixture_digest=manifest['fixture_digest'], fixture_manifest=manifest,
                                               cache_state='fresh destination; OS cache uncontrolled' if scenario == 'cold-clone' else 'OS cache warm/uncontrolled',
                                               raw_samples=raw, metrics=summarize([s['elapsed_ms'] for s in raw]), correctness='passed'))
                    write_json(output, result)
                shutil.rmtree(source)
        result['status'] = 'complete'
        validate_result(result)
        if args.baseline:
            baseline = json.loads(result_path(args.baseline).read_text())
            result['comparison'] = compare(result, baseline)
            result['baseline_commit'] = baseline['commit']
        write_json(output, result)
        report(result, output.with_suffix('.html'), baseline if args.baseline else None)
        if isinstance(result['comparison'], list) and any(c['gate'] == 'block' for c in result['comparison']):
            return 1
        return 0
    except Exception as error:
        result['status'] = 'invalid'
        result['error'] = str(error)
        write_json(output, result)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fixture = commands.add_parser('fixtures')
    fixture.add_argument('--fixture', choices=CATALOG, required=True)
    fixture.add_argument('--output', type=Path, required=True)
    fixture.add_argument('--seed', type=int, default=SEED)
    fixture.add_argument('--profile', choices=('full', 'smoke'), default='full')
    benchmark = commands.add_parser('bench')
    benchmark.add_argument('--suite', choices=('quick', 'all', *CATALOG), default='quick')
    benchmark.add_argument('--profile', choices=('full', 'smoke'), default='full')
    benchmark.add_argument('--repetitions', type=int, default=30)
    benchmark.add_argument('--scenarios')
    benchmark.add_argument('--baseline')
    benchmark.add_argument('--output', default='latest')
    reports = commands.add_parser('perf-report')
    reports.add_argument('--input', default='latest')
    reports.add_argument('--compare')
    inventory = commands.add_parser('_inventory')
    inventory.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'fixtures':
            generate(args.fixture, args.output.resolve(), args.seed, args.profile)
            validate_fixture(args.output.resolve())
        elif args.command == 'bench':
            return bench(args)
        elif args.command == 'perf-report':
            path = result_path(args.input)
            result = json.loads(path.read_text())
            comparison = json.loads(result_path(args.compare).read_text()) if args.compare else None
            rows = report(result, path.with_suffix('.html'), comparison)
            return int(any(r['gate'] == 'block' for r in rows))
        else:
            index = json.loads((args.path / 'index.json').read_text())
            for record in index['repositories']:
                if record['bundle'] and digest(args.path / record['bundle']) != record['bundle']:
                    raise ValueError('Invalid inventory dependency')
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Benchmark error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
