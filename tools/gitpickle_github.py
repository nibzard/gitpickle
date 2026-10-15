"""Authenticated GitHub discovery and durable protection for the reference service."""
import base64
import copy
import json
import http.client
import socket
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import gitpickle_archive as a
import gitpickle_security as security
from gitpickle_jobs import JobError


class GitHub:
    """Tokens come from a runtime provider. Redirects never receive credentials."""
    origin = 'https://api.github.com'

    def __init__(self, token, *, installation=False, transport=None, clock=time.time):
        self.token, self.installation, self.clock = token, installation, clock
        self.transport = transport or self._transport

    @staticmethod
    def _transport(url, headers):
        connection = None
        try:
            parsed = urlsplit(url)
            addresses = security.github_addresses(parsed.hostname)
            connection = http.client.HTTPSConnection(parsed.hostname, timeout=60)
            # Connect to the checked address while retaining the original hostname
            # for TLS certificate validation and the HTTP Host header.
            connection._create_connection = lambda address, timeout, source_address=None: socket.create_connection(
                (addresses[0], 443), timeout, source_address)
            target = parsed.path + ('?' + parsed.query if parsed.query else '')
            connection.request('GET', target, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(16 * 1024 * 1024 + 1)
        except (http.client.HTTPException, OSError, ValueError):
            raise JobError('source_unavailable') from None
        finally:
            if connection is not None:
                connection.close()

    def credential(self):
        token = self.token()
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise JobError('authentication')
        return token

    def request(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'api.github.com'
                or parsed.fragment or parsed.username):
            raise JobError('invalid_pagination')
        token = self.credential()
        status, headers, body = self.transport(url, {
            'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'GitPickle'})
        headers = {k.lower(): v for k, v in headers.items()}
        if status == 429 or (status == 403 and ('retry-after' in headers
                or headers.get('x-ratelimit-remaining') == '0')):
            try:
                delay = max(60, float(headers.get('retry-after', 0)),
                            float(headers.get('x-ratelimit-reset', 0)) - self.clock())
            except ValueError:
                delay = 60
            raise JobError('rate_limited', delay)
        if status == 401:
            raise JobError('authentication')
        if status == 403:
            raise JobError('forbidden')
        if status == 404:
            raise JobError('source_unavailable')
        if status != 200:
            raise JobError('source_unavailable')
        try:
            if len(body) > 16 * 1024 * 1024:
                raise ValueError()
            return json.loads(body), headers
        except (ValueError, TypeError):
            raise JobError('invalid_response') from None

    @staticmethod
    def repository(value):
        if (not isinstance(value, dict) or type(value.get('id')) is not int or value['id'] <= 0
                or not isinstance(value.get('full_name'), str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value['full_name'])):
            raise JobError('invalid_response')
        return {'id': value['id'], 'alias': value['full_name'],
                'has_wiki': value.get('has_wiki') is True}

    def repositories(self):
        route = '/installation/repositories' if self.installation else '/user/repos'
        url = self.origin + route + '?per_page=100&page=1'
        seen, result = set(), {}
        while url:
            if url in seen or len(seen) >= 10000:
                raise JobError('invalid_pagination')
            # A next link must keep the same endpoint, including before sending the token.
            if urlsplit(url).path != route:
                raise JobError('invalid_pagination')
            seen.add(url)
            body, headers = self.request(url)
            rows = body.get('repositories') if self.installation and isinstance(body, dict) else body
            if not isinstance(rows, list):
                raise JobError('invalid_response')
            for row in rows:
                repo = self.repository(row)
                identity = str(repo['id'])
                if identity in result and result[identity] != repo:
                    raise JobError('inventory_changed')
                result[identity] = repo
            links = re.findall(r'<([^>]+)>\s*;\s*rel="next"', headers.get('link', ''))
            if len(links) > 1:
                raise JobError('invalid_pagination')
            url = links[0] if links else None
        return result

    def get(self, identity):
        value, _ = self.request(self.origin + '/repositories/' + str(identity))
        repo = self.repository(value)
        if repo['id'] != identity:
            raise JobError('identity_conflict')
        return repo

    def execute(self, service, job):
        """Refresh a temporary mirror. Persist neither source credentials nor its config."""
        request = job['request']
        identity = request['arguments']['github_id']
        repo = self.get(identity)
        env = security.git_environment()
        credential = base64.b64encode(('x-access-token:' + self.credential()).encode()).decode()
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
                   GIT_CONFIG_COUNT='4', GIT_CONFIG_KEY_0='http.https://github.com/.extraHeader',
                   GIT_CONFIG_VALUE_0='Authorization: Basic ' + credential,
                   GIT_CONFIG_KEY_1='http.followRedirects', GIT_CONFIG_VALUE_1='false',
                   GIT_CONFIG_KEY_2='credential.helper', GIT_CONFIG_VALUE_2='', GIT_ALLOW_PROTOCOL='https',
                   GIT_CONFIG_KEY_3='http.curloptResolve',
                   GIT_CONFIG_VALUE_3='github.com:443:' + ','.join(
                       '[' + addr + ']' if ':' in addr else addr
                       for addr in security.github_addresses('github.com')))
        staging = service.root / '.projection' / 'github-staging'
        staging.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=staging,
                prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'github') + '-') as directory:
            target = Path(directory) / 'source.git'
            try:
                security.run(['git', *security.GIT_OPTIONS, 'clone', '--mirror', '--template=', '--',
                                'https://github.com/' + repo['alias'] + '.git', str(target)],
                               env=env, capture_output=True, check=True, timeout=3600)
            except (subprocess.SubprocessError, OSError):
                raise JobError('source_unavailable') from None
            # A rename/name reuse during clone must not capture a different repository.
            if self.get(identity)['alias'] != repo['alias']:
                raise JobError('source_unavailable')
            service.checkpoint(job['id'])
            if request['operation'] == 'sync':
                return service.observe(job, target)
            lfs = self.fetch_lfs(target, repo['alias'], env)
            wiki = None
            if repo['has_wiki']:
                wiki = False
                wiki_target = Path(directory) / 'wiki.git'
                try:
                    security.run(['git', *security.GIT_OPTIONS, 'clone',
                                    '--mirror', '--template=', '--',
                                    'https://github.com/' + repo['alias'] + '.wiki.git', str(wiki_target)],
                                   env=env, capture_output=True, check=True, timeout=3600)
                    a.git(wiki_target, 'fsck', '--full', '--strict')
                except (subprocess.SubprocessError, OSError):
                    pass  # Enabled but unavailable is required, incomplete coverage.
                else:
                    current = self.get(identity)
                    if current['alias'] != repo['alias'] or current['has_wiki'] != repo['has_wiki']:
                        raise JobError('source_unavailable')
                    wiki_lfs = self.fetch_lfs(wiki_target, repo['alias'] + '.wiki', env)
                    wiki = service.writer.capture(wiki_target, request['repository'] + '-wiki',
                        request['source'] + ':wiki', wiki_lfs, operation_id=job['id'] + ':wiki')
            # Bind both clones to the stable identity before publication.
            current = self.get(identity)
            if current['alias'] != repo['alias'] or current['has_wiki'] != repo['has_wiki']:
                raise JobError('source_unavailable')
            service.checkpoint(job['id'])
            return service.writer.capture(target, request['repository'], request['source'], lfs,
                                          operation_id=job['id'], wiki=wiki)

    @staticmethod
    def fetch_lfs(target, alias, env):
        required = a.required_lfs(target)
        if not required:
            return None
        # Override repository endpoint and storage settings. Never use submodule URLs.
        storage = target / 'lfs'
        try:
            security.run(['git', *security.GIT_OPTIONS, '-C', str(target),
                            '-c', 'lfs.url=https://github.com/' + alias + '.git/info/lfs',
                            '-c', 'lfs.storage=' + str(storage), 'lfs', 'fetch', '--all', 'origin'],
                           env=env, capture_output=True, check=True, timeout=3600)
        except (subprocess.SubprocessError, OSError):
            pass  # Capture validates each object; a partial transfer retains Git progress.
        return storage / 'objects'


class Protection:
    """Use with an open Jobs session. Immutable records survive projection loss."""
    def __init__(self, jobs, github):
        self.jobs, self.github = jobs, github
        self.root = jobs.root
        self.reload()

    def reload(self):
        records, children = {}, {}
        for path in (self.root / 'protection' / 'github').glob('*'):
            key = str(path.relative_to(self.root))
            value = a.read(self.root, key, 'protection')
            if value['archive_id'] != self.jobs.archive_id or value['parent'] in children:
                raise ValueError('conflicting protection records')
            records[key] = value
            children[value['parent']] = key
        self.head, self.state, count = None, {
            'repositories': {}, 'policy': None, 'discovery_due': 0, 'error': None}, 0
        while self.head in children:
            self.head = children[self.head]
            self.state = records[self.head]['value']
            count += 1
        if count != len(records):
            raise ValueError('broken protection chain')
        if self.head is not None:
            a.durable_put(self.root, self.head, a.read(self.root, self.head))

    def save(self, value):
        if not self.jobs.writer.writable:
            raise PermissionError('archive is read-only')
        self.head = a.record(self.root, 'protection/github', {
            'kind': 'protection', 'archive_id': self.jobs.archive_id,
            'parent': self.head, 'value': value})
        self.state = value

    def preview(self, identities=None, *, all_authorized=False, include_future=False,
                capture_interval=6 * 3600, discovery_interval=24 * 3600):
        """Return a concrete plan. Applying it requires its digest and a fresh inventory."""
        if (identities is None) == (not all_authorized) or (include_future and not all_authorized):
            raise JobError('explicit_scope_required')
        if any(type(interval) is not int or interval <= 0
               for interval in (capture_interval, discovery_interval)):
            raise JobError('invalid_input')
        inventory = self.github.repositories()
        selected = sorted(inventory if all_authorized else {str(i) for i in identities})
        if not selected or not set(selected) <= inventory.keys():
            raise JobError('source_unavailable')
        plan = {'repositories': {i: inventory[i] for i in selected},
                'include_future': include_future, 'capture_interval': capture_interval,
                'discovery_interval': discovery_interval}
        return {'plan': plan, 'confirmation': a.digest(a.encode(plan))}

    def protect(self, preview, confirmation):
        with self.jobs.lock:
            self.reload()
            plan = preview['plan']
            if (set(plan) != {'repositories', 'include_future', 'capture_interval', 'discovery_interval'}
                    or type(plan['include_future']) is not bool
                    or any(type(plan[field]) is not int or plan[field] <= 0
                           for field in ('capture_interval', 'discovery_interval'))
                    or not isinstance(plan['repositories'], dict) or not plan['repositories']
                    or any(not re.fullmatch('[1-9][0-9]*', i) for i in plan['repositories'])):
                raise JobError('invalid_input')
            if confirmation != a.digest(a.encode(plan)):
                raise JobError('confirmation_required')
            current = self.github.repositories()
            if any(current.get(i) != repo for i, repo in plan['repositories'].items()):
                raise JobError('plan_stale')
            value = copy.deepcopy(self.state)
            value['policy'] = copy.deepcopy(plan)
            self._merge(value, current)
            value['discovery_due'] = self.jobs.clock() + plan['discovery_interval']
            self.save(value)
            return copy.deepcopy(value)

    def _merge(self, value, inventory):
        now, policy = self.jobs.clock(), value['policy']
        selected = set(policy['repositories'])
        if policy['include_future']:
            selected.update(inventory)
            selected.update(i for i, r in value['repositories'].items() if r['protected'])
        for i in selected:
            if i not in value['repositories']:
                value['repositories'][i] = {'repository_id': 'github-' + i, 'aliases': [],
                    'capture_due': now, 'available': False}
        for i, repo in value['repositories'].items():
            repo['protected'] = i in selected
            repo['available'] = i in inventory
            if i in inventory:
                repo.update(inventory[i])
                if repo['alias'] not in repo['aliases']:
                    repo['aliases'].append(repo['alias'])
                repo['observed_at'] = now
        value['error'] = None

    def sync(self):
        """Discover source identities and availability; never publish a recovery point."""
        with self.jobs.lock:
            self.reload()
            if not self.state['policy']:
                raise JobError('protection_required')
            value = copy.deepcopy(self.state)
            try:
                inventory = self.github.repositories()
            except JobError as error:
                value['error'] = error.code
                if error.code in ('authentication', 'forbidden'):
                    for repo in value['repositories'].values():
                        repo['available'] = False
                value['discovery_due'] = self.jobs.clock() + (error.retry_after or 300)
                self.save(value)
                raise
            self._merge(value, inventory)
            value['discovery_due'] = self.jobs.clock() + value['policy']['discovery_interval']
            self.save(value)
            return copy.deepcopy(value)

    def backup(self, identities=None, *, all_protected=False, key, staging_bytes):
        """Accept captures now. This does not change the ongoing policy."""
        with self.jobs.lock:
            self.reload()
            if (identities is None) == (not all_protected):
                raise JobError('explicit_scope_required')
            selected = sorted(i for i, r in self.state['repositories'].items() if r['protected']) if all_protected else [str(i) for i in identities]
            if not selected or any(i not in self.state['repositories'] or not self.state['repositories'][i]['protected'] for i in selected):
                raise JobError('invalid_scope')
            from gitpickle_hosted import state
            if any(state(self.root, 'github-' + i)[1]['mode'] == 'hosted' for i in selected):
                raise JobError('source_updates_paused')
            return [self.jobs.accept('protection', 'backup', key + ':' + i, 'github-' + i,
                    'github', {'github_id': int(i)}, staging_bytes=staging_bytes) for i in selected]

    def tick(self, *, staging_bytes):
        """Call from the daemon alongside Jobs.tick; retries retain the same slot key."""
        with self.jobs.lock:
            self.reload()
            if not self.state['policy']:
                return []
            now = self.jobs.clock()
            if self.state['discovery_due'] <= now:
                try:
                    self.sync()
                except JobError:
                    pass
            if self.state['error']:
                return []
            value, accepted = copy.deepcopy(self.state), []
            from gitpickle_hosted import state
            for i, repo in value['repositories'].items():
                if state(self.root, 'github-' + i)[1]['mode'] == 'hosted':
                    continue
                if repo['protected'] and repo['available'] and repo['capture_due'] <= now:
                    due = repo['capture_due']
                    accepted.append(self.jobs.accept('protection', 'backup',
                        'scheduled:' + str(due) + ':' + i, 'github-' + i, 'github',
                        {'github_id': int(i)}, staging_bytes=staging_bytes))
                    repo['capture_due'] = now + value['policy']['capture_interval']
            if accepted:
                self.save(value)
            return accepted
