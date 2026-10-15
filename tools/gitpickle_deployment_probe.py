"""Bounded HTTPS client fixture for local deployment acceptance, not a public proxy."""
import base64
import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import threading
import tempfile
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import gitpickle_archive as archive


# This retained program needs no checkout. Match UID, command, and session leader.
PROCESS_CLEANUP = '''
import os, pathlib, signal, sys, time
executable, state = sys.argv[1:]
expected = [executable, 'serve', '--state', state]
def owned(pid):
    try:
        p = pathlib.Path('/proc') / str(pid)
        if p.stat().st_uid != os.getuid() or os.getpgid(pid) != pid:
            return False
        args = p.joinpath('cmdline').read_bytes().decode().split('\\0')
        if args[:4] == expected:
            return True
        # The release executable replaces itself with its embedded Python runtime.
        environment = p.joinpath('environ').read_bytes().split(b'\\0')
        return (len(args) > 5 and pathlib.Path(args[0]).name == 'python3'
                and args[1:3] == ['-I', '-B'] and pathlib.Path(args[3]).name == 'runtime.py'
                and args[4:6] == ['--state', state]
                and ('GITPICKLE_EXECUTABLE=' + executable).encode() in environment)
    except (OSError, UnicodeError):
        return False
for p in pathlib.Path('/proc').iterdir():
    if p.name.isdigit() and owned(int(p.name)):
        pid = int(p.name)
        try:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 30
            while owned(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if owned(pid):
                os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
'''


def create_fixture(source):
    """Create a fixed Git history for local and fresh-host deployment drills."""
    source.mkdir(mode=0o700, parents=True)
    archive.git(source, 'init', '--template=', '--initial-branch=main')
    (source / 'data').write_text('durable deployment fixture')
    archive.git(source, 'add', 'data')
    environment = archive.security.git_environment()
    environment.update(GIT_AUTHOR_DATE='2000-01-01T00:00:00+00:00',
                       GIT_COMMITTER_DATE='2000-01-01T00:00:00+00:00')
    archive.security.run(['git', *archive.security.GIT_OPTIONS, '-C', str(source),
        '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-m', 'fixture'], env=environment)
    return archive.git(source, 'rev-parse', 'HEAD').decode().strip()


@contextmanager
def https_fixture(base):
    """Terminate test TLS on loopback; forward bounded requests to one daemon."""
    cert, key = base / 'cert.pem', base / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-days', '1', '-subj', '/CN=127.0.0.1',
                    '-addext', 'subjectAltName=IP:127.0.0.1',
                    '-keyout', str(key), '-out', str(cert)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key.chmod(0o600)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.backend, timeout=60)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 1024 * 1024:
                    self.send_error(413)
                    return
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ('host', 'connection', 'transfer-encoding')}
                connection.request(self.command, self.path, self.rfile.read(size), headers)
                response = connection.getresponse()
                body = response.read(64 * 1024 * 1024 + 1)
                if len(body) > 64 * 1024 * 1024:
                    self.send_error(502)
                    return
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in ('connection', 'content-length', 'transfer-encoding'):
                        self.send_header(name, value)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
            finally:
                connection.close()

        do_POST = do_GET

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.backend = None
    server.url = f'https://127.0.0.1:{server.server_port}/git'
    server.cert = cert
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def git_probe(proxy, base, token, stage, expected_head):
    """Measure actual clone/fetch; assert integrity and repository access controls."""
    auth = 'Basic ' + base64.b64encode(('reader:' + token).encode()).decode()
    environment = archive.security.git_environment()
    environment.update(GIT_ALLOW_PROTOCOL='https', GIT_SSL_CAINFO=str(proxy.cert),
                       GIT_LFS_SKIP_SMUDGE='1', GIT_CONFIG_COUNT='1',
                       GIT_CONFIG_KEY_0='http.extraHeader',
                       GIT_CONFIG_VALUE_0='Authorization: ' + auth)
    clone = base / ('clone-' + stage)

    def git(*args, authenticated=True, success=True):
        env = dict(environment)
        if not authenticated:
            env['GIT_CONFIG_COUNT'] = '0'
        started = time.monotonic()
        result = subprocess.run(['git', *args], env=env, capture_output=True, timeout=60)
        elapsed = round(time.monotonic() - started, 6)
        assert (result.returncode == 0) == success, result.stderr.decode()
        return elapsed

    samples = dict(first_clone_seconds=git('clone', proxy.url + '/r.git', str(clone)))
    assert (clone / 'data').read_text() == 'durable deployment fixture'
    assert archive.git(clone, 'rev-parse', 'HEAD').decode().strip() == expected_head
    samples['warm_fetch_seconds'] = [git('-C', str(clone), 'fetch', 'origin') for _ in range(3)]
    git('-C', str(clone), 'fsck', '--full', '--strict')
    git('ls-remote', proxy.url + '/r.git', authenticated=False, success=False)
    git('ls-remote', proxy.url + '/denied.git', success=False)
    git('-C', str(clone), 'push', 'origin', 'main', success=False)
    statuses = {}
    for name, repository, header, expected in (
            ('anonymous', 'r', None, 401), ('invalid_token', 'r', 'Basic cmVhZGVyOmJhZA==', 401),
            ('denied_repository', 'denied', auth, 403)):
        request = urllib.request.Request(proxy.url + '/' + repository + '.git/info/refs?service=git-upload-pack',
                                         headers={'Authorization': header} if header else {})
        try:
            urllib.request.urlopen(request, context=ssl.create_default_context(cafile=str(proxy.cert)), timeout=10)
            raise AssertionError('Unexpected access: ' + name)
        except urllib.error.HTTPError as error:
            assert error.code == expected, (name, error.code)
            statuses[name] = error.code
    return dict(**samples, authorization=statuses, strict_fsck=True, mirror_push_rejected=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fixture = commands.add_parser('fixture')
    fixture.add_argument('--directory', type=Path, required=True)
    client = commands.add_parser('client')
    client.add_argument('--url', required=True, help='Canonical HTTPS URL ending in /git')
    client.add_argument('--expected-head', required=True)
    client.add_argument('--ca', type=Path, default=Path(ssl.get_default_verify_paths().cafile))
    client.add_argument('--token-env', default='GITPICKLE_TEST_READER_TOKEN')
    client.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'fixture':
        print(json.dumps(dict(repository='r', head=create_fixture(args.directory.resolve()))))
    else:
        if not args.url.startswith('https://') or not args.url.endswith('/git'):
            parser.error('Use the canonical HTTPS URL ending in /git')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=args.output.parent,
                prefix=os.environ['LOOPER_RESOURCE_PREFIX'] + '-') as folder:
            result = git_probe(SimpleNamespace(url=args.url, cert=args.ca), Path(folder),
                               os.environ[args.token_env], 'client', args.expected_head)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(args.output)


if __name__ == '__main__':
    main()
