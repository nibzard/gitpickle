"""Inspect this shared development machine and exercise bounded, private resources."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]

# Keep recovery independent of this checkout and of the caller's environment.
CLEANUP = r'''
import json, os, pathlib, shutil, subprocess, sys
path, prefix, token, artifact, container = sys.argv[1:]
p = pathlib.Path(path)
if container:
    result = subprocess.run(['docker', 'container', 'inspect', container], capture_output=True, text=True)
    if result.returncode and not any(message in result.stderr.lower()
                                     for message in ('no such container', 'no such object')):
        raise RuntimeError('Cannot inspect owned container: ' + result.stderr)
    if result.returncode == 0:
        labels = json.loads(result.stdout)[0]['Config']['Labels'] or {}
        expected = {'io.looper.resource-prefix': prefix, 'io.looper.artifact': artifact,
                    'io.gitpickle.dev-token': token}
        if all(labels.get(k) == v for k, v in expected.items()):
            subprocess.run(['docker', 'container', 'rm', '-f', container], check=True)
if p.exists() and not p.is_symlink() and p.name == 'gitpickle-' + prefix + '-' + token:
    marker = p / '.gitpickle-owner.json'
    if not marker.is_symlink() and marker.is_file() and p.stat().st_uid == os.getuid():
        expected = {'prefix': prefix, 'token': token, 'artifact': artifact, 'uid': os.getuid()}
        if json.loads(marker.read_text()) == expected:
            shutil.rmtree(p)
'''


def command(args, *, env=None, cwd=ROOT, timeout=30):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {result.stderr.strip()}')
    return (result.stdout + result.stderr).strip()


def probe(args):
    try:
        return {'available': True, 'version': command(args)}
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return {'available': False, 'error': str(error)}


def snapshot():
    """Hash source and unignored files, including uncommitted implementation files."""
    names = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
    result = {}
    for name in set(names.decode().split('\0')) - {''}:
        path = ROOT / name
        if path.is_symlink():
            result[name] = ('symlink', os.readlink(path))
        elif path.is_file():
            result[name] = (path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            result[name] = None
    return result


def inventory():
    tools = {name: probe(args) for name, args in {
        'rustc': ['rustc', '--version'], 'cargo': ['cargo', '--version'],
        'rustfmt': ['rustfmt', '--version'], 'clippy': ['cargo', 'clippy', '--version'],
        'git': ['git', '--version'], 'git_lfs': ['git', 'lfs', 'version'],
        'python3': ['python3', '--version'], 'cc': ['cc', '--version'],
        'java': ['java', '-version'], 'docker': ['docker', 'version', '--format', '{{.Server.Version}}'],
        'podman': ['podman', '--version'], 'hyperfine': ['hyperfine', '--version'],
    }.items()}
    cgroup = Path('/proc/self/cgroup')
    limits = {}
    if cgroup.exists():
        for line in cgroup.read_text().splitlines():
            if line.startswith('0::'):
                base = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
                for directory in [base, *base.parents]:
                    if directory == Path('/sys/fs'):
                        break
                    for name in ('cpu.max', 'memory.max', 'pids.max'):
                        path = directory / name
                        if path.is_file():
                            limits[str(path)] = path.read_text().strip()
    disks = {str(p): dict(zip(('total', 'used', 'free'), shutil.disk_usage(p)))
             for p in (ROOT, Path(tempfile.gettempdir()))}
    return {'platform': platform.platform(), 'architecture': platform.machine(),
            'logical_cpus': os.cpu_count(),
            'available_cpus': len(os.sched_getaffinity(0)),
            'cpu_model': next((line.split(':', 1)[1].strip() for line in
                               Path('/proc/cpuinfo').read_text().splitlines()
                               if line.startswith('model name')), 'unknown'),
            'memory': Path('/proc/meminfo').read_text().splitlines()[:3],
            'cgroup_limits': limits, 'disk_bytes': disks, 'tools': tools,
            'allocation': 'shared; no exclusive CPU, disk, or network allocation',
            'scope': 'local correctness and small exploratory benchmarks only'}


def register_cleanup(path, prefix, token, artifact, container, cleanup_file):
    args = [sys.executable, '-c', CLEANUP, str(path), prefix, token, artifact, container]
    # Append and sync before allocating. Never replace existing setup cleanup.
    with Path(cleanup_file).open('a') as stream:
        stream.write('\n# GitPickle isolated development resources\n' + shlex.join(args) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return args


def exercise(path, prefix, image_id, container):
    # Git fixtures cannot inherit hooks, filters, credentials, or host identity.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(HOME=str(path), XDG_CONFIG_HOME=str(path / 'config'),
               TMPDIR=str(path), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='1',
               GIT_CONFIG_KEY_0='core.hooksPath', GIT_CONFIG_VALUE_0=os.devnull)
    repo = path / 'repo'
    command(['git', 'init', '--initial-branch=main', str(repo)], env=env)
    command(['git', '-c', 'user.name=GitPickle test', '-c', 'user.email=test@invalid',
             'commit', '--allow-empty', '-m', 'Isolated test'], env=env, cwd=repo)
    command(['git', 'fsck', '--strict'], env=env, cwd=repo)
    command(['git', 'lfs', 'version'], env=env, cwd=repo)
    with sqlite3.connect(path / (prefix + '.sqlite3')) as database:
        database.execute('create table isolation (value text)')
        database.execute('insert into isolation values (?)', (prefix,))
        assert database.execute('select value from isolation').fetchone() == (prefix,)
    # The kernel selects the free port; keep the socket open until the check ends.
    with socket.socket() as server, socket.socket() as client:
        server.settimeout(5)
        client.settimeout(5)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        endpoint = server.getsockname()
        client.connect(endpoint)
        connection, _ = server.accept()
        with connection:
            connection.settimeout(5)
            client.sendall(b'gitpickle')
            assert connection.recv(32) == b'gitpickle'
    if image_id:
        command(['docker', 'create', '--pull=never', '--name', container,
                 '--label', 'io.looper.resource-prefix=' + prefix,
                 '--label', 'io.looper.artifact=' + os.environ['LOOPER_ARTIFACT_DIR'],
                 '--label', 'io.gitpickle.dev-token=' + path.name.rsplit('-', 1)[1],
                 '--network=none', '--read-only', '--cap-drop=ALL',
                 '--security-opt=no-new-privileges', '--memory=32m', '--cpus=0.25',
                 '--pids-limit=16', '--user=65534:65534', '--entrypoint=/bin/sh',
                 image_id, '-c', 'printf gitpickle-isolated'])
        output = command(['docker', 'start', '--attach', container])
        if output != 'gitpickle-isolated':
            raise RuntimeError('Container smoke output differs')
    return {'git': 'passed', 'lfs': 'passed', 'sqlite': 'passed',
            'loopback': {'status': 'passed', 'endpoint': list(endpoint), 'closed': True},
            'container': {'status': 'passed' if image_id else 'unavailable', 'image_id': image_id}}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--container-image', default='busybox:musl',
                        help='Use an already installed image; never pull or build')
    args = parser.parse_args()
    prefix = os.environ.get('LOOPER_RESOURCE_PREFIX', '')
    cleanup_file = os.environ.get('LOOPER_RESOURCE_CLEANUP_FILE', '')
    artifact = os.environ.get('LOOPER_ARTIFACT_DIR', '')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', prefix) or not cleanup_file or not artifact:
        parser.error('Set LOOPER_RESOURCE_PREFIX, LOOPER_RESOURCE_CLEANUP_FILE, and LOOPER_ARTIFACT_DIR')
    before = snapshot()
    token = uuid.uuid4().hex
    path = Path(tempfile.gettempdir()).resolve() / f'gitpickle-{prefix}-{token}'
    container = f'{prefix}-gitpickle-{token}'
    report = {'schema_version': 1, 'time': datetime.now(timezone.utc).isoformat(),
              'status': 'incomplete', 'resource_prefix': prefix, 'scratch': str(path)}
    cleanup = None
    try:
        report['inventory'] = inventory()
        required = ['rustc', 'cargo', 'rustfmt', 'clippy', 'git', 'git_lfs', 'python3', 'cc']
        missing = [name for name in required if not report['inventory']['tools'][name]['available']]
        if missing:
            raise RuntimeError('Missing development tools: ' + ', '.join(missing))
        if any(disk['free'] < 1024 ** 3 for disk in report['inventory']['disk_bytes'].values()):
            raise RuntimeError('Local smoke tests require 1 GiB of free disk')
        image_id = None
        if report['inventory']['tools']['docker']['available']:
            image = probe(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.container_image])
            if image['available']:
                image_id = image['version']
            else:
                report['container_unavailable'] = image['error']
        cleanup = register_cleanup(path, prefix, token, artifact,
                                   container if image_id else '', cleanup_file)
        path.mkdir(mode=0o700)
        (path / '.gitpickle-owner.json').write_text(json.dumps(
            dict(prefix=prefix, token=token, artifact=artifact, uid=os.getuid())))
        report['checks'] = exercise(path, prefix, image_id, container)
        report['status'] = 'passed'
    except (OSError, RuntimeError, subprocess.SubprocessError, AssertionError) as error:
        report.update(status='failed', error=str(error))
    finally:
        if cleanup:
            try:
                command(cleanup)
                if path.exists():
                    raise RuntimeError('Owned scratch directory remains after cleanup')
                report['cleanup'] = 'passed; registered cleanup remains idempotent'
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                report.update(status='failed', cleanup=str(error))
        report['source_unchanged'] = before == snapshot()
        if not report['source_unchanged']:
            report.update(status='failed', error='Tracked or unignored project files changed')
        output = ROOT / 'tests/results/dev-env' / f'{prefix}-{token}.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
        print(output)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
