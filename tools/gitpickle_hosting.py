"""Authenticated Git gateway over committed archive publications."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import zlib
from urllib.parse import urlsplit

import gitpickle_hosted as hosted
import gitpickle_archive as archive
import gitpickle_recovery as recovery
import gitpickle_security as security
from gitpickle_plans import PlanError


class GitEngine:
    """Bridge to the selected Rust GitEngine. It receives no credentials."""
    @contextmanager
    def smart_http(self, repository, method, endpoint, query, protocol, body):
        executable = os.environ['GITPICKLE_EXECUTABLE']
        with tempfile.TemporaryFile() as output:
            security.run([executable, '__git-http', str(repository), method,
                          endpoint, query, protocol], input=body, output=output,
                         env=security.git_environment(), timeout=130)
            output.seek(0)
            yield output


class Hosting:
    def __init__(self, plans, base_url, engine=None):
        url = urlsplit(base_url)
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or url.path != '/git' or any(ord(c) <= 32 for c in base_url)
                or base_url != f'https://{url.netloc}/git'):
            raise ValueError('canonical Git URL must be https://HOST/git')
        self.plans, self.base_url = plans, base_url
        self.engine = engine or GitEngine()

    def describe(self, principal, repository):
        with self.plans.policies.lock:
            self.plans._require(principal, 'git_read', [repository], self.plans.policies.current)
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', repository):
                raise PlanError('invalid_input')
            mode = hosted.state(self.plans.root, repository)[1]['mode']
            return dict(repository=repository, mode=mode, read_only=mode != 'hosted',
                        remote_url=f'{self.base_url}/{repository}.git',
                        head=hosted.head(self.plans.root, repository))

    @contextmanager
    def generation(self, principal, repository):
        # Recheck the grant for each request. Publication files, rather than
        # staging directories or source refs, select the visible generation.
        self.describe(principal, repository)
        chain = archive.inventory(self.plans.root, repository)
        if not chain:
            raise PlanError('not_found')
        point = archive.read(self.plans.root, chain[-1], 'publication')['point']
        info = recovery.point_info(self.plans.root, point, repository)
        if not info['complete']:
            raise PlanError('generation_unavailable')
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-hosting-') as folder:
            repo = Path(folder) / 'repository.git'
            archive.restore(self.plans.root, point, repo)
            yield repo, archive.read(self.plans.root, point, 'point')


def respond(handler, code, body, content_type='text/plain; charset=utf-8', authenticate=False):
    handler.send_response(code)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Connection', 'close')
    if authenticate:
        handler.send_header('WWW-Authenticate', 'Basic realm="GitPickle", charset="UTF-8"')
    handler.end_headers()
    handler.wfile.write(body)
    handler.close_connection = True


def stream(handler, code, source, length, content_type):
    handler.close_connection = True
    # Once transfer starts, a failed connection must close, never append an error.
    try:
        handler.send_response(code)
        handler.send_header('Content-Type', content_type)
        handler.send_header('Content-Length', str(length))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        shutil.copyfileobj(source, handler.wfile, 64 * 1024)
    except OSError:
        pass


def body(handler, limit, *, allow_empty=False):
    lengths = handler.headers.get_all('Content-Length', [])
    if handler.headers.get('Transfer-Encoding') or len(lengths) != 1:
        raise PlanError('invalid_input')
    if not re.fullmatch('[0-9]+', lengths[0]) or not (0 if allow_empty else 1) <= int(lengths[0]) <= limit:
        raise PlanError('invalid_input')
    data = handler.rfile.read(int(lengths[0]))
    if len(data) != int(lengths[0]):
        raise PlanError('invalid_input')
    return data


def handle(handler):
    hosting = getattr(handler.server.application, 'hosting', None)
    if hosting is None:
        respond(handler, 404, b'Git hosting is disabled.\n')
        return
    try:
        headers = handler.headers.get_all('Authorization', [])
        if len(headers) != 1 or not headers[0].startswith('Basic '):
            raise PlanError('authentication')
        try:
            user, token = base64.b64decode(headers[0][6:], validate=True).decode('ascii').split(':', 1)
            if not user or not token:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise PlanError('authentication') from None
        principal = handler.server.application.authenticate('Bearer ' + token)
        if handler.headers.get('Origin') is not None:
            raise PlanError('invalid_input')
        match = re.fullmatch(r'/git/([A-Za-z0-9_-]{1,128})\.git/(info/refs\?service=git-(?:upload|receive)-pack|git-(?:upload|receive)-pack|info/lfs/objects/batch|info/lfs/objects/[0-9a-f]{64})', handler.path)
        if not match:
            raise PlanError('not_found')
        repository, endpoint = match.groups()
        description = hosting.describe(principal, repository)
        if 'receive-pack' in endpoint and description['read_only']:
            respond(handler, 403, b'Protected mirror is read-only. Push to the source repository; GitPickle captures its changes.\n')
            return
        if 'receive-pack' in endpoint:
            with hosting.plans.policies.lock:
                hosting.plans._require(principal, 'git_write', [repository], hosting.plans.policies.current)
            if handler.command == 'GET' and endpoint.startswith('info/refs?'):
                with hosting.generation(principal, repository) as (repo, _):
                    respond(handler, 200, hosted.advertise(repo), 'application/x-git-receive-pack-advertisement')
            elif handler.command == 'POST' and endpoint == 'git-receive-pack':
                if handler.headers.get_all('Content-Type', []) != ['application/x-git-receive-pack-request'] or handler.headers.get('Content-Encoding'):
                    raise PlanError('invalid_input')
                final = hosted.receive(hosting, principal, repository, body(handler, 64 * 1024 * 1024))
                respond(handler, 200, final, 'application/x-git-receive-pack-result')
            else:
                raise PlanError('invalid_input')
            return
        if handler.command == 'PUT' and endpoint.startswith('info/lfs/objects/'):
            with hosting.plans.policies.lock, hosting.plans.writer.mutation(repository):
                hosting.plans._require(principal, 'git_write', [repository], hosting.plans.policies.current)
                if hosted.state(hosting.plans.root, repository)[1]['mode'] != 'hosted':
                    raise PlanError('forbidden')
                payload = body(handler, 64 * 1024 * 1024, allow_empty=True)
                oid = endpoint.rsplit('/', 1)[1]
                if archive.digest(payload) != oid:
                    raise PlanError('invalid_input')
                hosting.plans._require(principal, 'git_write', [repository], hosting.plans.policies.current)
                archive.durable_put(hosting.plans.root, f'repos/{repository}/lfs/{oid}', payload)
            respond(handler, 200, b'')
            return
        if handler.command not in ('GET', 'POST'):
            raise PlanError('invalid_input')
        with hosting.generation(principal, repository) as (repo, point):
            if endpoint.startswith('info/lfs/'):
                lfs(handler, hosting, repository, point, endpoint, principal)
                return
            query = 'service=git-upload-pack' if endpoint.startswith('info/refs?') else ''
            endpoint = endpoint.split('?')[0]
            if (handler.command, endpoint) not in (('GET', 'info/refs'), ('POST', 'git-upload-pack')):
                raise PlanError('invalid_input')
            if handler.command == 'POST' and handler.headers.get_all('Content-Type', []) != ['application/x-git-upload-pack-request']:
                raise PlanError('invalid_input')
            protocols = handler.headers.get_all('Git-Protocol', [])
            if len(protocols) > 1 or (protocols and protocols[0] not in ('version=1', 'version=2')):
                raise PlanError('invalid_input')
            request = body(handler, 1024 * 1024) if handler.command == 'POST' else b''
            encodings = handler.headers.get_all('Content-Encoding', [])
            if encodings:
                if handler.command != 'POST' or encodings != ['gzip']:
                    raise PlanError('invalid_input')
                try:
                    decoder = zlib.decompressobj(31)
                    request = decoder.decompress(request, 1024 * 1024 + 1)
                    if len(request) > 1024 * 1024 or not decoder.eof or decoder.unused_data:
                        raise ValueError('compressed request exceeds limit')
                except (ValueError, zlib.error):
                    raise PlanError('invalid_input') from None
            with hosting.engine.smart_http(repo, handler.command, endpoint, query,
                                           protocols[0] if protocols else '', request) as response:
                head = bytearray()
                while not head.endswith(b'\r\n\r\n'):
                    chunk = response.read(1)
                    if not chunk or len(head) >= 65536:
                        raise ValueError('invalid backend headers')
                    head.extend(chunk)
                fields = dict(line.decode('ascii').split(': ', 1) for line in head[:-4].split(b'\r\n'))
                code = int(fields.get('Status', '200').split()[0])
                length = os.fstat(response.fileno()).st_size - response.tell()
                stream(handler, code, response, length, fields.get('Content-Type', 'application/octet-stream'))
    except PlanError as error:
        codes = {'authentication': 401, 'forbidden': 403, 'not_found': 404,
                 'invalid_input': 400, 'conflict': 409, 'required_lfs_missing': 422,
                 'generation_unavailable': 503}
        message = ('required_lfs_missing: Upload required LFS objects, then retry the push.'
                   if error.code == 'required_lfs_missing' else error.code)
        respond(handler, codes.get(error.code, 503), (message + '\n').encode(),
                authenticate=error.code == 'authentication')
    except Exception:
        respond(handler, 503, b'Committed generation unavailable. Check archive integrity and server limits.\n')


def lfs(handler, hosting, repository, point, endpoint, principal):
    """Git Large File Storage (LFS) basic transfer with durable uploads."""
    if endpoint == 'info/lfs/objects/batch' and handler.command == 'POST':
        request = json.loads(body(handler, 65536))
        upload = request.get('operation') == 'upload'
        if upload:
            with hosting.plans.policies.lock:
                hosting.plans._require(principal, 'git_write', [repository], hosting.plans.policies.current)
                if hosted.state(hosting.plans.root, repository)[1]['mode'] != 'hosted':
                    raise PlanError('forbidden')
        if request.get('operation') not in ('download', 'upload'):
            respond(handler, 403, b'Protected mirror is read-only. LFS uploads are disabled.\n')
            return
        if ('transfers' in request and 'basic' not in request['transfers']
                or not isinstance(request.get('objects'), list) or len(request['objects']) > 100):
            raise PlanError('invalid_input')
        objects = []
        for obj in request['objects']:
            oid, size = obj['oid'], obj['size']
            if not isinstance(oid, str) or not re.fullmatch('[0-9a-f]{64}', oid) or type(size) is not int or size < 0:
                raise PlanError('invalid_input')
            result = dict(oid=oid, size=size, authenticated=True)
            if upload:
                result['actions'] = {'upload': {'href': f'{hosting.base_url}/{repository}.git/info/lfs/objects/{oid}'}}
            elif oid in point['lfs'] and size == point['lfs'][oid]['size']:
                result['actions'] = {'download': {'href': f'{hosting.base_url}/{repository}.git/info/lfs/objects/{oid}'}}
            else:
                result['error'] = dict(code=404, message='Object is not in this committed generation')
            objects.append(result)
        respond(handler, 200, json.dumps(dict(transfer='basic', objects=objects)).encode(),
                'application/vnd.git-lfs+json')
    elif handler.command == 'GET' and re.fullmatch('info/lfs/objects/[0-9a-f]{64}', endpoint):
        oid = endpoint.rsplit('/', 1)[1]
        if oid not in point['lfs']:
            raise PlanError('not_found')
        obj = point['lfs'][oid]
        with archive.verified_file(hosting.plans.root, obj['key'], obj['size']) as source:
            stream(handler, 200, source, obj['size'], 'application/octet-stream')
    else:
        raise PlanError('invalid_input')
