"""Compare a linked walgit probe with stock Git HTTP on fixed, small fixtures.

Build tools/walgit_probe.rs in a private Cargo project with path dependencies on
~/walgit. Pass its executable to this runner. No external storage is mutated.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import gitpickle_archive as archive
import gitpickle_perf as fixtures
import gitpickle_security as security

TOKEN = 'fixed-local-spike-token'
AUTH = 'Basic ' + base64.b64encode(('spike:' + TOKEN).encode()).decode()


def environment(auth=True):
    env = security.git_environment()
    env.update(GIT_ALLOW_PROTOCOL='http:file', GIT_AUTHOR_NAME='Fixture',
               GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_NAME='Fixture',
               GIT_COMMITTER_EMAIL='fixture@example.invalid',
               GIT_AUTHOR_DATE='2000-01-01T00:00:00Z', GIT_COMMITTER_DATE='2000-01-01T00:00:00Z')
    if auth:
        env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.extraHeader',
                   GIT_CONFIG_VALUE_0='Authorization: ' + AUTH)
    return env


def git(repo, *args, ok=True, auth=True, data=None):
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args],
                            input=data, capture_output=True, env=environment(auth), timeout=60)
    if ok and result.returncode:
        raise RuntimeError(result.stderr.decode())
    return result


class StockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_git()

    def do_POST(self):
        self.handle_git()

    def handle_git(self):
        if self.headers.get('Authorization') != AUTH:
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="spike"')
            self.end_headers()
            return
        url = urlsplit(self.path)
        if '..' in url.path.split('/') or self.headers.get('Transfer-Encoding'):
            self.send_error(400)
            return
        size = int(self.headers.get('Content-Length', '0'))
        env = environment(False)
        env.update(GIT_PROJECT_ROOT=str(self.server.root), GIT_HTTP_EXPORT_ALL='1',
                   PATH_INFO=url.path, QUERY_STRING=url.query, REQUEST_METHOD=self.command,
                   CONTENT_TYPE=self.headers.get('Content-Type', ''), CONTENT_LENGTH=str(size),
                   REMOTE_USER='spike', REMOTE_ADDR='127.0.0.1',
                   HTTP_GIT_PROTOCOL=self.headers.get('Git-Protocol', ''))
        response = subprocess.run(['git', 'http-backend'], input=self.rfile.read(size),
                                  env=env, capture_output=True, timeout=60)
        headers, body = response.stdout.split(b'\r\n\r\n', 1)
        pairs = [line.decode().split(':', 1) for line in headers.split(b'\r\n')]
        status = next((int(v.strip().split()[0]) for k, v in pairs if k.lower() == 'status'), 200)
        self.send_response(status)
        for key, value in pairs:
            if key.lower() != 'status':
                self.send_header(key, value.strip())
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def stock(root):
    root.mkdir()
    server = ThreadingHTTPServer(('127.0.0.1', 0), StockHandler)
    server.root = root
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Probe:
    def __init__(self, binary, root):
        self.log = (root / 'probe.log').open('w')
        self.process = subprocess.Popen([str(binary), str(root / 'cache')],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=self.log, text=True,
                                       env=dict(environment(), GITPICKLE_SPIKE_TOKEN=TOKEN))
        try:
            self.url = self.line()
        except BaseException:
            self.close()
            raise

    def line(self):
        import select
        if not select.select([self.process.stdout], [], [], 60)[0]:
            raise TimeoutError('probe response timeout')
        line = self.process.stdout.readline().strip()
        if not line:
            raise RuntimeError('probe stopped; inspect probe.log')
        return line

    def command(self, name):
        self.process.stdin.write(name + '\n')
        self.process.stdin.flush()
        value = self.line()
        if name == 'restart':
            self.url = value
        else:
            assert value == 'ready', value

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()
        self.log.close()


def create(base, name, fmt, root=None):
    if root is None:
        with urlopen(Request(f'{base}/t/{name}?object_format={fmt}', method='PUT',
                             headers={'Authorization': AUTH}), timeout=30) as response:
            assert response.status in (200, 201, 204)
    else:
        target = root / 't' / (name + '.git')
        target.mkdir(parents=True)
        git(target, 'init', '--bare', '--template=', '--initial-branch=main', '--object-format=' + fmt)
        git(target, 'config', 'http.receivepack', 'true')
    return f'{base}/t/{name}.git'


def lfs_roundtrip(base):
    payload = b'fixed GitPickle LFS fixture\n' * 128
    oid = hashlib.sha256(payload).hexdigest()
    def batch(operation):
        request = Request(base + '/t/r1.git/info/lfs/objects/batch',
                          data=json.dumps(dict(operation=operation, transfers=['basic'],
                                               objects=[dict(oid=oid, size=len(payload))])).encode(),
                          headers={'Authorization': AUTH, 'Content-Type': 'application/vnd.git-lfs+json'})
        with urlopen(request, timeout=30) as response:
            return json.load(response)['objects'][0]
    upload = batch('upload')['actions']['upload']
    headers = dict(upload.get('header', {}), Authorization=AUTH)
    with urlopen(Request(upload['href'], data=payload, method='PUT', headers=headers), timeout=30) as response:
        assert response.status in (200, 201, 204)
    download = batch('download')['actions']['download']
    with urlopen(Request(download['href'], headers=dict(download.get('header', {}), Authorization=AUTH)), timeout=30) as response:
        assert response.read() == payload
    assert not batch('upload').get('actions'), 'Existing LFS content must not require re-upload'


def run(binary, root):
    samples = []
    portfolio_samples = []
    sources = []
    # A bounded mixed portfolio. Full-size portfolio measurements belong to T051.
    for index in range(8):
        path = root / f'fixture-{index}.git'
        fixtures.make_repo(path, index, 0 if index == 0 else 3, 4096 * index)
        sources.append(path)
    sha256 = root / 'sha256.git'
    sha256.mkdir()
    git(sha256, 'init', '--bare', '--template=', '--object-format=sha256', '--initial-branch=main')
    tree = git(sha256, 'mktree', data=b'').stdout.strip().decode()
    commit = git(sha256, 'commit-tree', tree, data=b'sha256 fixture\n').stdout.strip().decode()
    git(sha256, 'update-ref', 'refs/heads/main', commit)
    sources.append(sha256)
    inventory = []
    for source in sources:
        measured = fixtures.measure_repo(source, 0)
        inventory.append({key: measured[key] for key in
                          ('name', 'object_digest', 'object_count', 'packed_bytes', 'logical_bytes', 'refs')})
    probe = Probe(binary, root)
    try:
        with stock(root / 'stock') as stock_url:
            for engine, base in [('stock-git-http', stock_url), ('walgit', probe.url)]:
                for index, source in enumerate(sources):
                    fmt = 'sha256' if source == sha256 else 'sha1'
                    url = create(base, f'r{index}', fmt, root / 'stock' if engine == 'stock-git-http' else None)
                    if index:
                        git(source, 'push', '--mirror', url)
                    denied = git(root, 'ls-remote', url, ok=False, auth=False)
                    assert denied.returncode, 'Unauthenticated read succeeded'
                    for protocol in ('0', '2'):
                        for repeat in range(3):
                            dest = root / f'{engine}-{index}-{protocol}-{repeat}'
                            start = time.monotonic_ns()
                            git(root, '-c', 'protocol.version=' + protocol, 'clone', '--mirror', url, str(dest))
                            elapsed = time.monotonic_ns() - start
                            assert fixtures.refs(source) == fixtures.refs(dest)
                            git(dest, 'fsck', '--full', '--strict')
                            assert git(dest, 'rev-parse', '--show-object-format').stdout.strip().decode() == fmt
                            samples.append(dict(engine=engine, fixture=index, protocol=protocol,
                                                repeat=repeat, clone_ns=elapsed, correct=True))
                # Exercise all 404 namespaces with bounded payloads. Reuse the
                # same fixed sources to isolate engine behavior from generation.
                started = time.monotonic_ns()
                for index in range(404):
                    source = sources[1 + index % 3] if index < 400 else sources[index - 396]
                    url = create(base, f'portfolio-{index}', 'sha1',
                                 root / 'stock' if engine == 'stock-git-http' else None)
                    git(source, 'push', '--mirror', url)
                    expected = {name: oid for name, oid in (line.split() for line in fixtures.refs(source))}
                    actual = {name: oid for oid, name in (line.split() for line in
                              git(root, 'ls-remote', '--refs', url).stdout.decode().splitlines())}
                    assert actual == expected
                    if index % 100 == 0 or index >= 400:
                        clone = root / f'{engine}-portfolio-{index}'
                        git(root, 'clone', '--mirror', url, str(clone))
                        git(clone, 'fsck', '--full', '--strict')
                portfolio_samples.append(dict(engine=engine, repositories=404,
                                              capture_and_inventory_ns=time.monotonic_ns() - started,
                                              correct=True))
                source = sources[1]
                url = f'{base}/t/r1.git'
                old = git(source, 'rev-parse', 'refs/heads/main').stdout.strip().decode()
                tree = git(source, 'rev-parse', old + '^{tree}').stdout.strip().decode()
                new = git(source, 'commit-tree', tree, data=b'rewritten root\n').stdout.strip().decode()
                # Atomic reject must not create topic; ordinary partial push may create it.
                git(source, 'push', url, '+' + new + ':refs/heads/main')
                result = git(source, 'push', '--atomic', url, 'main:main', 'main:refs/heads/new-topic', ok=False)
                assert result.returncode
                assert b'refs/heads/new-topic' not in git(root, 'ls-remote', url).stdout
                result = git(source, 'push', url, 'main:main', 'main:refs/heads/new-topic', ok=False)
                assert result.returncode
                refs = git(root, 'ls-remote', url).stdout
                assert b'refs/heads/new-topic' in refs and (new + '\trefs/heads/main').encode() in refs
                git(source, 'push', '--force-with-lease=refs/heads/main:' + new, url, 'main:main')
                git(source, 'push', url, ':refs/heads/new-topic', ':refs/tags/v1')
                restored = root / (engine + '-rewritten')
                git(root, 'clone', '--mirror', url, str(restored))
                store = root / (engine + '-archive')
                archive.initialize(store, 'spike')
                point = archive.capture(store, restored, 'r', 'fixture')
                git(source, 'push', url, '+' + new + ':refs/heads/main')
                git(restored, 'fetch', '--prune', url, '+refs/*:refs/*')
                second = archive.capture(store, restored, 'r', 'fixture')
                shutil.rmtree(restored)
                archive.restore(store, point, root / (engine + '-old'))
                archive.restore(store, second, root / (engine + '-new'))
                assert git(root / (engine + '-old'), 'rev-parse', 'main').stdout.strip().decode() == old
                assert git(root / (engine + '-new'), 'rev-parse', 'main').stdout.strip().decode() == new
            # Lose the walgit cache after an acknowledged push. Keep only emulated storage.
            probe.command('restart')
            url = probe.url + '/t/r1.git'
            recovered = root / 'cold-walgit'
            git(root, 'clone', '--mirror', url, str(recovered))
            git(recovered, 'fsck', '--full', '--strict')
            assert git(recovered, 'rev-parse', 'main').stdout.strip().decode() == new
            probe.command('lost')
            git(sources[1], 'push', url, 'main:refs/heads/lost-reply')
            probe.command('heal')
            probe.command('restart')
            assert b'refs/heads/lost-reply' in git(root, 'ls-remote', probe.url + '/t/r1.git').stdout
            probe.command('fail')
            result = git(sources[1], 'push', probe.url + '/t/r1.git', 'main:refs/heads/must-not-ack', ok=False)
            assert result.returncode, 'Storage failure acknowledged a push'
            probe.command('heal')
            probe.command('restart')
            assert b'refs/heads/must-not-ack' not in git(root, 'ls-remote', probe.url + '/t/r1.git').stdout
            lfs_roundtrip(probe.url)
        return dict(schema_version=1, fixture_version=1, samples=samples,
                    fixtures=inventory,
                    portfolio_samples=portfolio_samples,
                    portfolio_fixture='400 small namespaces plus four larger namespaces; fixed source reuse',
                    fixed_source_count=9, storage='walgit MemoryStore with FaultStore',
                    environment=dict(platform=platform.platform(), cpu_count=os.cpu_count(),
                                     probe_profile='unoptimized development build',
                                     provider_latency='none; emulated storage',
                                     timing_scope='local observations; no release performance gate'),
                    checks=['private HTTP credentials', 'SHA-1/SHA-256', 'protocol v0/v2',
                            'atomic rejection', 'partial multi-ref results', 'force-with-lease',
                            'branch/tag deletion', 'historical snapshot restore',
                            'acknowledged push after cache loss', 'lost manifest reply reconciliation',
                            'storage failure cannot acknowledge a push', 'LFS basic upload/download'],
                    git_version=subprocess.check_output(['git', '--version'], text=True).strip(),
                    deferred=['full portfolio/full-size performance: T051', 'live S3/B2: T054'])
    finally:
        probe.close()


def main():
    from gitpickle_dev_env import snapshot
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path)
    parser.add_argument('--walgit', type=Path, default=Path.home() / 'walgit')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    before = snapshot()
    fork = args.walgit.resolve()
    if not (fork / 'Cargo.toml').is_file():
        parser.error('Prerequisite failure: the specified walgit checkout is unavailable')
    revision = subprocess.check_output(['git', '-C', str(fork), 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output(['git', '-C', str(fork), 'status', '--porcelain']):
        parser.error('The spike requires a clean fork checkout')
    if args.probe is None:
        build = Path('.cache/git-engine-probe').resolve()
        (build / 'src').mkdir(parents=True, exist_ok=True)
        shutil.copyfile('tools/walgit_probe.rs', build / 'src/main.rs')
        shutil.copyfile(fork / 'Cargo.lock', build / 'Cargo.lock')
        manifest = ('[workspace]\n[package]\nname="gitpickle-engine-probe"\n'
                    'version="0.0.0"\nedition="2024"\n[dependencies]\nanyhow="1"\n'
                    'axum="0.8"\ntokio={version="1",features=["full"]}\n')
        for name in ('walgit-config', 'walgit-store', 'walgit-server'):
            manifest += name + '={path=' + json.dumps(str(fork / 'crates' / name)) + '}\n'
        (build / 'Cargo.toml').write_text(manifest)
        subprocess.run(['cargo', '+1.97.1', 'build', '--offline', '--manifest-path',
                        str(build / 'Cargo.toml'), '--target-dir', str(Path('.cache/walgit-target').resolve()),
                        '--jobs', '2'], check=True)
        args.probe = Path('.cache/walgit-target/debug/gitpickle-engine-probe')
    base = Path('fixtures/generated/engine-spike')
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'spike') + '-') as temp:
        result = run(args.probe.resolve(), Path(temp).resolve())
    result['walgit_revision'] = revision
    result['walgit_lock_sha256'] = hashlib.sha256((fork / 'Cargo.lock').read_bytes()).hexdigest()
    result['source_unchanged'] = snapshot() == before
    assert result['source_unchanged'], 'The spike changes tracked or unignored source files'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
