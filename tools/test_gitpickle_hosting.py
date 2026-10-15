"""Real Git HTTPS clients exercise the committed, read-only gateway."""
import base64
import http.client
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_daemon as daemon
import gitpickle_hosting as h
from gitpickle_plans import Grant, Plans, Policy, PolicyStore, Principal


class HostingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Standalone Python runs also exercise the real Rust engine boundary.
        subprocess.run(['cargo', 'build', '--locked', '-p', 'gitpickle'], check=True,
                       stdout=subprocess.DEVNULL)
        cls.binary = str(Path('target/debug/gitpickle').resolve())

    def setUp(self):
        base = Path('fixtures/generated/hosting')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'hosting-test')
        self.source = self.root / 'source.git'
        self.source.mkdir()
        a.git(self.source, 'init', '--bare', '--template=', '--initial-branch=main')
        self.commit(b'first')
        self.commit(b'second')
        a.git(self.source, 'tag', 'v1')
        self.publish()
        self.token = 'runtime-test-token-' + 'x' * 32
        self.policies = PolicyStore(Policy(1, (Grant('reader', 'git_read', frozenset({'r', 'empty', 'lfs', 'sha256'}), int(time.time()) + 3600),), frozenset(), str(self.root)))
        self.plans = self.enterContext(Plans(self.store, self.policies))
        self.app = daemon.Management(self.plans, {self.token: {'actor': 'reader', 'agent': False}})
        self.listener = daemon.server(self.app)
        self.addCleanup(self.listener.server_close)
        self.url = f'https://127.0.0.1:{self.listener.server_address[1]}/git'
        self.app.hosting = h.Hosting(self.plans, self.url)
        self.cert, key = self.root / 'cert.pem', self.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-keyout', str(key), '-out', str(self.cert)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self.listener.socket = context.wrap_socket(self.listener.socket, server_side=True)
        thread = threading.Thread(target=self.listener.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.listener.shutdown)
        self.enterContext(patch.dict(os.environ, GITPICKLE_EXECUTABLE=self.binary))
        self.auth = 'Basic ' + base64.b64encode(('reader:' + self.token).encode()).decode()
        self.env = a.security.git_environment()
        self.env.update(GIT_ALLOW_PROTOCOL='https', GIT_SSL_CAINFO=str(self.cert), GIT_LFS_SKIP_SMUDGE='1')

    def commit(self, data):
        blob = a.git(self.source, 'hash-object', '-w', '--stdin', data=data).decode().strip()
        entries = f'100644 blob {blob}\tfile\n'
        if data.startswith(b'version https://git-lfs.github.com/spec/v1'):
            attributes = a.git(self.source, 'hash-object', '-w', '--stdin',
                data=b'file filter=lfs diff=lfs merge=lfs -text\n').decode().strip()
            entries += f'100644 blob {attributes}\t.gitattributes\n'
        tree = a.git(self.source, 'mktree', data=entries.encode()).decode().strip()
        parents = a.git(self.source, 'for-each-ref', '--format=%(objectname)', 'refs/heads/main').decode().split()
        commit = a.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, *(['-p', parents[0]] if parents else []), data=b'hosting').decode().strip()
        a.git(self.source, 'update-ref', 'refs/heads/main', commit)
        return commit

    def publish(self, repo='r', lfs=None):
        # Use the active writer once the daemon owns the archive.
        if hasattr(self, 'plans'):
            return self.plans.writer.capture(self.source, repo, 'github:1', lfs=lfs)
        return a.capture(self.store, self.source, repo, 'github:1', lfs=lfs)

    def git(self, cwd, *args, auth=True, success=True, protocol=2):
        env = dict(self.env)
        if args and args[0] == 'lfs':
            env.pop('GIT_LFS_SKIP_SMUDGE', None)
        if auth:
            env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.extraHeader', GIT_CONFIG_VALUE_0='Authorization: ' + self.auth)
        result = subprocess.run(['git', '-c', 'protocol.version=' + str(protocol), '-C', str(cwd), *args],
                                env=env, capture_output=True, timeout=130)
        if success:
            self.assertEqual(0, result.returncode, result.stderr.decode())
        else:
            self.assertNotEqual(0, result.returncode)
        return result

    def request(self, path, method='GET', value=None, auth=True, extra_headers=None):
        connection = http.client.HTTPSConnection('127.0.0.1', self.listener.server_address[1],
            context=ssl.create_default_context(cafile=str(self.cert)), timeout=30)
        headers = {'Authorization': self.auth} if auth else {}
        payload = value if isinstance(value, bytes) else json.dumps(value).encode() if value is not None else None
        if payload:
            headers['Content-Type'] = 'application/vnd.git-lfs+json'
        headers.update(extra_headers or {})
        try:
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_clone_fetch_pull_tags_and_committed_visibility(self):
        clone = self.root / 'clone'
        self.git(self.root, 'clone', self.url + '/r.git', str(clone))
        self.assertEqual('second', (clone / 'file').read_text())
        self.assertIn(b'v1', self.git(clone, 'tag').stdout)
        self.commit(b'not committed to archive')
        self.git(clone, 'fetch')
        self.git(clone, 'pull', '--ff-only')
        self.assertEqual('second', (clone / 'file').read_text())
        self.publish()
        self.git(clone, 'fetch')
        self.git(clone, 'pull', '--ff-only')
        self.assertEqual('not committed to archive', (clone / 'file').read_text())
        self.git(clone, 'fsck', '--full', '--strict')
        for args in [('push', 'origin', 'main'), ('push', '--force-with-lease', 'origin', 'main')]:
            result = self.git(clone, *args, success=False)
            self.assertIn(b'read-only', result.stderr)

    def test_versions_shallow_and_advertised_features(self):
        for version in (0, 1, 2):
            clone = self.root / ('v' + str(version))
            self.git(self.root, 'clone', '--depth=1', self.url + '/r.git', str(clone), protocol=version)
            self.assertEqual(b'1\n', self.git(clone, 'rev-list', '--count', 'HEAD').stdout)
            self.git(clone, 'fetch', '--unshallow', protocol=version)
            self.assertEqual(b'2\n', self.git(clone, 'rev-list', '--count', 'HEAD').stdout)
        code, advertisement, _ = self.request('/git/r.git/info/refs?service=git-upload-pack')
        self.assertEqual(200, code)
        self.assertIn(b'shallow', advertisement)
        self.assertNotIn(b'filter ', advertisement)
        self.assertNotIn(b'push-cert', advertisement)

    def test_authentication_authorization_and_disabled_hosting(self):
        target = '/git/r.git/info/refs?service=git-upload-pack'
        self.assertEqual(401, self.request(target, auth=False)[0])
        original = self.auth
        self.auth = 'Basic ' + base64.b64encode(b'reader:wrong').decode()
        self.assertEqual(401, self.request(target)[0])
        self.auth = original
        self.git(self.root, 'ls-remote', self.url + '/r.git', auth=False, success=False)
        self.assertEqual(403, self.request('/git/denied.git/info/refs?service=git-upload-pack')[0])
        for target_bad in ('/git/../r.git/info/refs?service=git-upload-pack', '/git/%2e%2e.git/info/refs?service=git-upload-pack', '/git/r.git/HEAD'):
            self.assertEqual(404, self.request(target_bad)[0])
        info = self.app.call(Principal('reader'), 'hosting_status', {'repository': 'r'})
        self.assertEqual(self.url + '/r.git', info['remote_url'])
        self.assertEqual('protected_mirror', info['mode'])
        self.policies.replace(Policy(2, (), frozenset(), str(self.root)))
        self.assertEqual(403, self.request(target)[0])
        self.app.hosting = None
        self.assertEqual(404, self.request(target)[0])

    def test_compressed_protocol_request_and_expansion_limit(self):
        import gzip
        command = b'command=ls-refs\n'
        request = f'{len(command) + 4:04x}'.encode() + command + b'00010000'
        headers = {'Git-Protocol': 'version=2', 'Content-Encoding': 'gzip',
                   'Content-Type': 'application/x-git-upload-pack-request'}
        code, payload, _ = self.request('/git/r.git/git-upload-pack', 'POST', gzip.compress(request),
                                        extra_headers=headers)
        self.assertEqual(200, code)
        self.assertIn(b'refs/heads/main', payload)
        self.assertEqual(400, self.request('/git/r.git/git-upload-pack', 'POST',
            gzip.compress(b'x' * (1024 * 1024 + 1)), extra_headers=headers)[0])

    def test_empty_and_sha256(self):
        for repo, algorithm in [('empty', 'sha1'), ('sha256', 'sha256')]:
            self.source = self.root / (repo + '.git')
            self.source.mkdir()
            a.git(self.source, 'init', '--bare', '--template=', '--initial-branch=main', '--object-format=' + algorithm)
            if algorithm == 'sha256':
                self.commit(b'SHA-256')
            self.publish(repo)
            clone = self.root / (repo + '-clone')
            self.git(self.root, 'clone', self.url + '/' + repo + '.git', str(clone))
            if algorithm == 'sha256':
                self.assertEqual(b'sha256\n', self.git(clone, 'rev-parse', '--show-object-format').stdout)
            else:
                self.assertEqual(b'', self.git(clone, 'for-each-ref').stdout)
                self.assertEqual(b'refs/heads/main\n', self.git(clone, 'symbolic-ref', 'HEAD').stdout)

    def test_installed_executable_displays_mode_and_remote(self):
        import select
        state = self.root / 'installed-state'
        a.initialize(state, 'installed-test')
        a.capture(state, self.source, 'r', 'github:1')
        policy = self.root / 'policy.json'
        policy.write_text(json.dumps(dict(revision=1, grants=[dict(actor='reader',
            permission='git_read', repositories=['r'], expires_at=int(time.time()) + 300)],
            approvers=[], sandbox=str(self.root))))
        policy.chmod(0o600)
        binary = self.root / 'gitpickle'
        shutil.copy2(self.binary, binary)
        env = dict(os.environ, GITPICKLE_MANAGEMENT_TOKENS=json.dumps({self.token: {'actor': 'reader'}}),
                   GITPICKLE_MANAGEMENT_TOKEN=self.token)
        process = subprocess.Popen([str(binary), 'serve', '--state', str(state), '--policy', str(policy),
            '--enable-git', '--git-base-url', 'https://git.example.invalid/git'],
            cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 15)[0], 'daemon startup timed out')
            line = process.stdout.readline()
            self.assertTrue(line, 'installed daemon failed to start')
            port = json.loads(line)['listen'][1]
            result = subprocess.run([str(binary), 'hosting', '--server', f'http://127.0.0.1:{port}',
                '--repository', 'r', '--json'], cwd=self.root, env=env, capture_output=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr)
            description = json.loads(result.stdout)['data']
            self.assertEqual('https://git.example.invalid/git/r.git', description['remote_url'])
            self.assertEqual('protected_mirror', description['mode'])
            result = subprocess.run([str(binary), 'hosting', '--server', f'http://127.0.0.1:{port}',
                '--repository', 'r'], cwd=self.root, env=env, capture_output=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(b'Remote URL: https://git.example.invalid/git/r.git\n', result.stdout)
            # The installed daemon also contains the gateway and engine bridge.
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
            try:
                connection.request('GET', '/git/r.git/info/refs?service=git-upload-pack',
                    headers={'Authorization': self.auth})
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertIn(b'refs/heads/main', response.read())
            finally:
                connection.close()
        finally:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    def test_git_credential_helper_answers_authentication_challenge(self):
        helper = self.root / 'credential-helper'
        helper.write_text('#!/bin/sh\nprintf "username=reader\\npassword=%s\\n" "$GITPICKLE_TEST_TOKEN"\n')
        helper.chmod(0o700)
        env = dict(self.env, GITPICKLE_TEST_TOKEN=self.token)
        result = subprocess.run(['git', '-c', 'credential.helper=' + str(helper), 'ls-remote',
            self.url + '/r.git'], env=env, capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(b'refs/heads/main', result.stdout)
        self.assertNotIn(self.token.encode(), result.stdout + result.stderr)

    def test_unpublished_point_and_incomplete_generation_are_unavailable(self):
        before = self.git(self.root, 'ls-remote', self.url + '/r.git').stdout
        self.commit(b'unpublished')
        original_record = a.record
        def interrupt(root, directory, value):
            if value.get('kind') == 'publication':
                raise RuntimeError('interrupted before publication')
            return original_record(root, directory, value)
        with patch.object(a, 'record', side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.publish()
        self.assertEqual(before, self.git(self.root, 'ls-remote', self.url + '/r.git').stdout)
        self.commit(b'version https://git-lfs.github.com/spec/v1\noid sha256:' + b'a' * 64 + b'\nsize 3\n')
        self.publish()
        self.assertEqual(503, self.request('/git/r.git/info/refs?service=git-upload-pack')[0])

    def test_downloads_above_64_mib(self):
        import hashlib
        import random
        random_bytes = random.Random(57).randbytes
        # One capture must preserve a pack set above the diagnostic stdout cap.
        for _ in range(2):
            self.commit(random_bytes(33 * 1024 * 1024))
        self.publish()
        clone = self.root / 'large-clone'
        self.git(self.root, 'clone', self.url + '/r.git', str(clone))
        self.git(clone, 'fsck', '--full', '--strict')
        packs = list((clone / '.git/objects/pack').glob('*.pack'))
        self.assertGreater(sum(p.stat().st_size for p in packs), 64 * 1024 * 1024)
        expected = self.git(self.source, 'rev-parse', 'HEAD').stdout
        self.assertEqual(expected, self.git(clone, 'rev-parse', 'HEAD').stdout)

        self.source = self.root / 'large-lfs-source.git'
        self.source.mkdir()
        a.git(self.source, 'init', '--bare', '--template=', '--initial-branch=main')
        directory = self.root / 'large-lfs'
        directory.mkdir()
        payload = directory / 'payload'
        checksum = hashlib.sha256()
        with payload.open('wb') as output:
            for _ in range(65):
                chunk = random_bytes(1024 * 1024)
                checksum.update(chunk)
                output.write(chunk)
        oid, size = checksum.hexdigest(), payload.stat().st_size
        payload.rename(directory / oid)
        self.commit(f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {size}\n'.encode())
        point = self.publish('lfs', directory)
        shutil.rmtree(directory)
        lfs_clone = self.root / 'large-lfs-clone'
        self.git(self.root, 'clone', self.url + '/lfs.git', str(lfs_clone))
        self.git(lfs_clone, 'lfs', 'install', '--local')
        self.git(lfs_clone, 'lfs', 'pull')
        with (lfs_clone / 'file').open('rb') as source:
            actual = hashlib.sha256()
            while chunk := source.read(65536):
                actual.update(chunk)
        self.assertEqual(oid, actual.hexdigest())
        target = '/git/lfs.git/info/lfs/objects/' + oid
        self.assertEqual(401, self.request(target, auth=False)[0])
        key = a.read(self.store, point, 'point')['lfs'][oid]['key']
        with (self.store / key).open('r+b') as output:
            output.write(b'corrupt')
        self.assertEqual(503, self.request(target)[0])

    def test_durable_lfs_download_and_missing_content(self):
        payload = b'large content from archive'
        oid = a.digest(payload)
        directory = self.root / 'lfs'
        directory.mkdir()
        (directory / oid).write_bytes(payload)
        self.commit(f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode())
        point = self.publish('lfs', directory)
        shutil.rmtree(directory)
        clone = self.root / 'lfs-clone'
        self.git(self.root, 'clone', self.url + '/lfs.git', str(clone))
        # Real Git LFS basic transfer uses the normal Git credential machinery.
        self.git(clone, 'lfs', 'install', '--local')
        self.git(clone, 'lfs', 'pull')
        self.assertEqual(payload, (clone / 'file').read_bytes())
        batch = dict(operation='download', transfers=['basic'], objects=[dict(oid=oid, size=len(payload))])
        path = '/git/lfs.git/info/lfs/objects/batch'
        code, result, _ = self.request(path, 'POST', batch)
        self.assertEqual(200, code)
        href = json.loads(result)['objects'][0]['actions']['download']['href']
        self.assertEqual(self.url + '/lfs.git/info/lfs/objects/' + oid, href)
        self.assertEqual(payload, self.request('/git/lfs.git/info/lfs/objects/' + oid)[1])
        self.assertEqual(401, self.request('/git/lfs.git/info/lfs/objects/' + oid, auth=False)[0])
        self.assertEqual(403, self.request(path, 'POST', dict(batch, operation='upload'))[0])
        key = a.read(self.store, point, 'point')['lfs'][oid]['key']
        (self.store / key).unlink()
        self.assertEqual(503, self.request(path, 'POST', batch)[0])



    def enable_hosted(self, repository='r', action='convert'):
        grants = tuple(Grant('reader', permission, frozenset({'r', 'new'}), int(time.time()) + 3600)
                       for permission in ('git_read', 'git_write', 'hosting_manage'))
        self.policies.replace(Policy(self.policies.current.revision + 1, grants, frozenset(), str(self.root)))
        return self.app.call(Principal('reader'), 'hosting_change', dict(
            repository=repository, action=action, expected_head=h.hosted.head(self.store, repository)))

    def test_hosted_push_and_explicit_reconciliation(self):
        checkpoint = self.enable_hosted()['checkpoint']
        self.assertEqual('hosted', self.app.hosting.describe(Principal('reader'), 'r')['mode'])
        self.commit(b'hosted change')
        self.git(self.source, 'push', self.url + '/r.git', 'main', 'refs/tags/v1')
        with self.assertRaises(PermissionError):
            self.publish()
        clone = self.root / 'hosted-clone'
        self.git(self.root, 'clone', self.url + '/r.git', str(clone))
        self.assertEqual(b'hosted change', (clone / 'file').read_bytes())
        with self.assertRaises(h.PlanError):
            h.hosted.change(self.app.hosting, Principal('reader'), 'r', 'resume', checkpoint)
        self.enable_hosted(action='resume')
        self.publish()
        self.assertIn(checkpoint, a.inventory(self.store, 'r'))

    def test_hosted_storage_failure_and_missing_lfs_reject_push(self):
        self.enable_hosted()
        before = h.hosted.head(self.store, 'r')
        self.commit(b'cannot acknowledge')
        original = a.put
        def fail(root, key, data):
            if '/packs/' in key:
                raise OSError('injected storage failure')
            return original(root, key, data)
        with patch.object(a, 'put', fail):
            self.git(self.source, 'push', self.url + '/r.git', 'main', success=False)
        self.assertEqual(before, h.hosted.head(self.store, 'r'))
        pointer = b'version https://git-lfs.github.com/spec/v1\noid sha256:' + b'a' * 64 + b'\nsize 7\n'
        self.commit(pointer)
        self.git(self.source, 'push', '--no-verify', self.url + '/r.git', 'main', success=False)
        self.assertEqual(before, h.hosted.head(self.store, 'r'))

    def test_hosted_empty_creation_and_authorization(self):
        with self.assertRaises(h.PlanError):
            h.hosted.change(self.app.hosting, Principal('reader'), 'empty', 'create', None)
        self.enable_hosted('new', 'create')
        self.git(self.source, 'push', self.url + '/new.git', 'main')
        clone = self.root / 'new-clone'
        self.git(self.root, 'clone', self.url + '/new.git', str(clone))
        self.assertEqual(b'second', (clone / 'file').read_bytes())

    def test_hosted_lfs_upload_is_durable(self):
        self.enable_hosted()
        payload = b'hosted LFS bytes'
        oid = a.digest(payload)
        path = '/git/r.git/info/lfs/objects/' + oid
        original = a.put
        def fail(root, key, data):
            if '/lfs/' in key:
                raise OSError('injected LFS storage failure')
            return original(root, key, data)
        with patch.object(a, 'put', fail):
            self.assertEqual(503, self.request(path, 'PUT', payload)[0])
        self.assertFalse((self.store / f'repos/r/lfs/{oid}').exists())
        self.assertEqual(200, self.request(path, 'PUT', payload)[0])
        self.assertEqual(payload, a.read(self.store, f'repos/r/lfs/{oid}'))
        code, batch, _ = self.request('/git/r.git/info/lfs/objects/batch', 'POST',
            dict(operation='upload', objects=[dict(oid=oid, size=len(payload))]))
        self.assertEqual(200, code)
        self.assertIn('upload', json.loads(batch)['objects'][0]['actions'])
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode()
        self.commit(pointer)
        local_lfs = self.source / 'lfs' / 'objects' / oid[:2] / oid[2:4]
        local_lfs.mkdir(parents=True)
        (local_lfs / oid).write_bytes(payload)
        self.git(self.source, 'lfs', 'push', '--all', self.url + '/r.git')
        self.git(self.source, 'push', '--no-verify', self.url + '/r.git', 'main')
        self.assertEqual(payload, self.request(path)[1])

    def test_hosted_lost_response_replays_committed_operation(self):
        self.enable_hosted()
        self.commit(b'replay')
        receive = h.hosted.receive
        requests = []
        def lose(hosting, principal, repository, request):
            requests.append(request)
            receive(hosting, principal, repository, request)
            raise ConnectionError('lost final response')
        with patch.object(h.hosted, 'receive', lose):
            self.git(self.source, 'push', self.url + '/r.git', 'main', success=False)
        before = h.hosted.head(self.store, 'r')
        final = receive(self.app.hosting, Principal('reader'), 'r', requests[0])
        self.assertIn(b'ok refs/heads/main', final)
        self.assertEqual(before, h.hosted.head(self.store, 'r'))
        shutil.rmtree(self.source)
        rebuilt = self.root / 'rebuilt-archive'
        shutil.copytree(self.store, rebuilt, ignore=shutil.ignore_patterns('.writer.lock', '.projection'))
        with Plans(rebuilt, self.policies) as plans:
            recovered = h.Hosting(plans, self.url)
            self.assertEqual('hosted', recovered.describe(Principal('reader'), 'r')['mode'])
            with recovered.generation(Principal('reader'), 'r') as (repo, _):
                self.assertEqual(b'replay', a.git(repo, 'show', 'main:file'))
        clone = self.root / 'replayed-clone'
        self.git(self.root, 'clone', self.url + '/r.git', str(clone))
        self.assertEqual(b'replay', (clone / 'file').read_bytes())


class StreamTests(unittest.TestCase):
    def test_disconnect_does_not_append_an_error_response(self):
        import io
        class Handler:
            close_connection = False
            responses = 0
            def send_response(inner, code):
                inner.responses += 1
            def send_header(inner, *args):
                pass
            def end_headers(inner):
                raise BrokenPipeError()
        handler = Handler()
        h.stream(handler, 200, io.BytesIO(b'payload'), 7, 'application/octet-stream')
        self.assertTrue(handler.close_connection)
        self.assertEqual(1, handler.responses)


if __name__ == '__main__':
    unittest.main()
