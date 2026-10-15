"""Guided CLI and shared workflow service. Secrets use runtime providers only."""
import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

import gitpickle_archive as a
from gitpickle_github import Protection
from gitpickle_jobs import JobError
from gitpickle_plans import PlanError, Principal
import gitpickle_status as status
import gitpickle_recovery as recovery


def envelope(data=None, repositories=(), archive=None, error=None, jobs=()):
    return dict(schema_version=1, observed_at_unix_seconds=int(time.time()),
        scope=dict(archive_id=archive, repository_ids=list(repositories)),
        outcome='failed' if error else ('queued' if jobs else 'ok'),
        job_id=jobs[0]['id'] if jobs else None, coverage_gaps=[], evidence_ids=[],
        next_action=None, data=data if not error else None,
        error=dict(code=error, message='Cannot complete the command. Check the scope and configuration.',
                   retry={'kind': 'never'}) if error else None)


class Workflow:
    """Daemon-owned service. All writes require a human principal and scoped grants."""
    def __init__(self, plans, jobs, github, staging_bytes, *, cloud=None):
        self.plans, self.jobs, self.github = plans, jobs, github
        self.root, self.staging_bytes = plans.root, staging_bytes
        self.cloud = cloud
        self.protection = Protection(jobs, github)
        self.lock = threading.RLock()
        self.source = jobs.source_adapter
        self.restore_futures = {}
        jobs.source_adapter = self

    def start_restore(self, principal, job):
        def execute():
            try:
                self.plans.execute(principal, job)
            except Exception:
                with self.plans._transaction():
                    if not self.plans._event(job, 'terminal'):
                        self.plans._record(f'jobs/{job}/terminal', {'result': 'restore_failed'})
        self.restore_futures[job] = self.jobs.pool.submit(execute)

    def execute(self, jobs, job):
        """Revalidate the caller's current grant immediately before source access."""
        actor = job['request']['actor']
        if actor == 'protection':
            self.protection.reload()
            candidates = []
            for path in (self.root / 'execution' / 'setup').glob('*'):
                plan = a.read(self.root, str(path.relative_to(self.root)), 'execution')['setup']
                if plan['preview']['plan'] == self.protection.state['policy']:
                    candidates.append(plan['actor'])
            if not candidates:
                raise JobError('forbidden')
            if len(set(candidates)) != 1:
                raise JobError('forbidden')
            actor = candidates[0]
        with self.plans.policies.lock:
            try:
                self.require(Principal(actor), job['request']['operation'],
                             [job['request']['repository']])
            except PlanError as error:
                raise JobError(error.code) from None
        return self.source.execute(jobs, job)

    def require(self, principal, permission, repositories):
        if principal.agent:
            raise PlanError('forbidden')
        self.plans._require(principal, permission, repositories, self.plans.policies.current)

    def selection(self, principal, args, permission):
        self.protection.reload()
        registered = self.protection.state['repositories']
        if type(args['all']) is not bool:
            raise PlanError('invalid_input')
        if args['all'] and args['repository']:
            raise PlanError('invalid_input')
        selected = sorted('github-' + i for i, r in registered.items() if r['protected']) if args['all'] else [args['repository']]
        if not selected or not all(selected):
            raise PlanError('explicit_scope_required')
        self.require(principal, permission, selected)
        if any(not r.startswith('github-') or r[7:] not in registered or not registered[r[7:]]['protected'] for r in selected):
            raise PlanError('invalid_scope')
        return selected

    def probe(self):
        if shutil.disk_usage(self.root).free < self.staging_bytes + self.jobs.reserve:
            raise PlanError('insufficient_staging_disk')
        if shutil.which('git') is None:
            raise PlanError('missing_git')
        directory = self.root / '.projection'
        # Isolated random prefix. Never probe retained archive objects.
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-probe-', dir=directory) as folder:
            path = Path(folder) / 'readback'
            payload = os.urandom(64)
            with path.open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if path.read_bytes() != payload:
                raise PlanError('storage_unavailable')
        result = dict(authentication='authorized_inventory', storage='filesystem_readback',
                    capabilities=['immutable_records', 'readback', 'single_writer'],
                    staging_bytes=self.staging_bytes, transfer_bytes=None, lfs_bytes=None,
                    transfer_charges='unknown', bucket_creation=False)
        if self.cloud:
            result.update(self.cloud.probe())
        return result

    def call(self, principal, operation, args):
        schemas = {
            'workflow_inventory': set(), 'workflow_resolve': {'alias'},
            'protect_preview': {'repositories', 'all', 'include_future'},
            'protect_resume': {'reference'}, 'protect_apply': {'reference', 'digest'},
            'capture_preview': {'repository', 'all', 'operation', 'key'},
            'capture_accept': {'reference', 'digest'},
            'capture_get': {'job'},
            'workflow_status': {'repository', 'all'},
            'workflow_doctor': {'repository', 'all'}, 'restore_inventory': set(),
        }
        if operation not in schemas or not isinstance(args, dict) or set(args) != schemas[operation]:
            raise PlanError('invalid_input')
        with self.lock, self.plans.policies.lock:
            if principal.agent:
                raise PlanError('forbidden')
            if operation == 'restore_inventory':
                inventory = recovery.storage_inventory(self.root)
                rows = []
                for row in inventory['repositories']:
                    try:
                        self.require(principal, 'plan_restore', [row['repository_id']])
                    except PlanError:
                        continue
                    rows.append({'repository_id': row['repository_id'],
                                 'complete_points': sum(p['complete'] for p in row['points'])})
                return rows
            if operation == 'workflow_resolve':
                self.protection.reload()
                matches = [r['repository_id'] for r in self.protection.state['repositories'].values()
                           if args['alias'] in r.get('aliases', []) and r['protected']]
                if len(matches) != 1:
                    raise PlanError('invalid_scope')
                self.require(principal, 'status', matches)
                return matches[0]
            if operation == 'workflow_inventory':
                inventory = self.github.repositories()
                # Discovery cannot expose names outside the granted protection scope.
                rows = {}
                for i, row in inventory.items():
                    try:
                        self.require(principal, 'protect', ['github-' + i])
                    except PlanError:
                        continue
                    rows[i] = row
                return rows
            if operation == 'protect_preview':
                if type(args['all']) is not bool or type(args['include_future']) is not bool:
                    raise PlanError('invalid_input')
                preview = self.protection.preview(args['repositories'], all_authorized=args['all'],
                                                  include_future=args['include_future'])
                ids = ['github-' + i for i in preview['plan']['repositories']]
                self.protection.reload()
                prior_ids = [r['repository_id'] for r in self.protection.state['repositories'].values() if r['protected']]
                self.require(principal, 'protect', sorted(set(ids + prior_ids)))
                self.require(principal, 'backup', ids)
                checks = self.probe()
                self.protection.reload()
                old = self.protection.state['policy']
                plan = dict(actor=principal.actor, preview=preview, checks=checks,
                    all=args['all'], expected_policy=old,
                    policy_revision=self.plans.policies.current.revision,
                    expires_at=int(time.time()) + 3600, time_zone='UTC',
                    changes=dict(add=sorted(set(ids) - {'github-' + i for i in (old or {}).get('repositories', {})}),
                                 stop=sorted({'github-' + i for i in (old or {}).get('repositories', {})} - set(ids))))
                reference = self.plans._record('setup', {'setup': plan})
                return dict(reference=reference, digest=a.digest(a.encode(plan)), plan=plan)
            if operation in ('protect_resume', 'protect_apply'):
                plan = self.plans._read(args['reference'], 'setup')['setup']
                ids = ['github-' + i for i in plan['preview']['plan']['repositories']]
                self.protection.reload()
                prior_ids = [r['repository_id'] for r in self.protection.state['repositories'].values() if r['protected']]
                self.require(principal, 'protect', sorted(set(ids + prior_ids)))
                self.require(principal, 'backup', ids)
                if plan['actor'] != principal.actor:
                    raise PlanError('forbidden')
                digest = a.digest(a.encode(plan))
                if operation == 'protect_resume':
                    return dict(reference=args['reference'], digest=digest, plan=plan)
                if args['digest'] != digest:
                    raise PlanError('conflict')
                # Completed setup is replayable without repeating source authorization.
                for path in (self.root / 'execution' / 'setup-results').glob('*'):
                    value = a.read(self.root, str(path.relative_to(self.root)), 'execution')
                    if value['reference'] == args['reference']:
                        if self.protection.state['policy'] != plan['preview']['plan']:
                            raise PlanError('plan_stale')
                        return value['result']
                if plan['expires_at'] <= time.time():
                    raise PlanError('expired_plan')
                if plan['policy_revision'] != self.plans.policies.current.revision:
                    raise PlanError('conflict')
                self.protection.reload()
                current = self.protection.state['policy']
                desired = plan['preview']['plan']
                if current != plan['expected_policy'] and current != desired:
                    raise PlanError('plan_stale')
                checks = self.probe()
                if any(checks[k] != plan['checks'][k] for k in ('storage', 'staging_bytes')):
                    raise PlanError('plan_stale')
                if plan['all']:
                    if self.github.repositories() != desired['repositories']:
                        raise PlanError('plan_stale')
                self.protection.protect(plan['preview'], plan['preview']['confirmation'])
                # The scheduling slot is durable. A retry after a lost reply returns
                # existing slot jobs instead of accepting another initial capture.
                accepted = self.protection.tick(staging_bytes=self.staging_bytes)
                if not accepted:
                    accepted = [j for j in self.jobs.jobs.values() if
                        j['request']['repository'] in ids and
                        j['request']['actor'] == 'protection']
                result = dict(repositories=ids, jobs=[self.public_job(j) for j in accepted],
                              protection='running', storage=self.cloud.location if self.cloud else 'filesystem',
                              recovery_card=recovery.recovery_card(self.root, self.cloud.location if self.cloud else None))
                self.plans._record('setup-results', {'reference': args['reference'], 'result': result})
                return result
            if operation in ('capture_preview', 'capture_accept'):
                reference = None
                if operation == 'capture_accept':
                    reference = args['reference']
                    plan = self.plans._read(reference, 'capture-plans')['capture']
                    if (plan['actor'] != principal.actor or args['digest'] != a.digest(a.encode(plan))
                            or plan['expires_at'] <= time.time()):
                        raise PlanError('conflict')
                    args = plan['arguments']
                if args['operation'] not in ('backup', 'sync') or not isinstance(args['key'], str) or not args['key']:
                    raise PlanError('invalid_input')
                ids = self.selection(principal, args, args['operation'])
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', args['key']):
                    raise PlanError('invalid_input')
                batch = dict(actor=principal.actor, operation=args['operation'], key=args['key'], repositories=ids)
                for path in (self.root / 'execution' / 'batches').glob('*'):
                    previous = a.read(self.root, str(path.relative_to(self.root)), 'execution')['batch']
                    if all(previous[k] == batch[k] for k in ('actor', 'operation', 'key')) and previous != batch:
                        raise PlanError('conflict')
                checks = self.probe()
                if operation == 'capture_preview':
                    inventory = self.github.repositories()
                    if any(i[7:] not in inventory for i in ids):
                        raise PlanError('source_unavailable')
                    plan = dict(actor=principal.actor, arguments=args, repositories=ids, checks=checks,
                                policy_revision=self.plans.policies.current.revision,
                                expires_at=int(time.time()) + 3600, time_zone='UTC')
                    reference = self.plans._record('capture-plans', {'capture': plan})
                    return dict(reference=reference, digest=a.digest(a.encode(plan)), plan=plan)
                if (ids != plan['repositories'] or plan['policy_revision'] != self.plans.policies.current.revision
                        or any(checks[k] != plan['checks'][k] for k in ('storage', 'staging_bytes'))):
                    raise PlanError('plan_stale')
                self.plans._record('batches', {'batch': batch})
                accepted = [self.jobs.accept(principal.actor, args['operation'], args['key'] + ':' + i,
                    i, 'github', {'github_id': int(i[7:])}, staging_bytes=self.staging_bytes) for i in ids]
                return dict(repositories=ids, jobs=[self.public_job(j) for j in accepted])
            if operation == 'capture_get':
                job = self.jobs.inspect(args['job'])
                self.require(principal, 'status', [job['request']['repository']])
                return self.public_job(job)
            if operation in ('workflow_status', 'workflow_doctor'):
                ids = self.selection(principal, args, 'status')
                result = status.calculate(self.root)
                result['scope']['repository_ids'] = ids
                data = result['data']
                data['repositories'] = [r for r in data['repositories'] if r['repository_id'] in ids]
                data['current_and_verified'] = sum(r['current_and_verified'] for r in data['repositories'])
                data['need_attention'] = len(ids) - data['current_and_verified']
                data['healthy'] = data['need_attention'] == 0 and bool(ids)
                data['restore_tested_repository_ids'] = [i for i in data['restore_tested_repository_ids'] if i in ids]
                result['coverage_gaps'] = [g for g in result['coverage_gaps'] if g['repository_id'] in ids]
                result['evidence_ids'] = sorted({v['evidence_id'] for r in data['repositories'] for v in
                    [c['verification'] for c in r['coverage'].values()] + r['restore_tests'] if v})
                result['next_action'] = next((r['next_action'] for r in data['repositories'] if r['next_action']), None)
                if operation == 'workflow_doctor':
                    self.github.repositories()
                    result['data']['configuration_checks'] = self.probe()
                return result

    @staticmethod
    def public_job(job):
        return {key: job[key] for key in ('id', 'state', 'error', 'accepted_at')}


def checkout_alias():
    try:
        remote = a.git(Path.cwd(), 'remote', 'get-url', 'origin').decode().strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.fullmatch(r'(?:https://github.com/|git@github.com:|ssh://git@github.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', remote)
    return match[1] if match else None


class Client:
    def __init__(self, server, token):
        parsed = urlsplit(server)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise PlanError('configuration')
        self.port, self.token = parsed.port or 80, token

    def call(self, operation, arguments):
        hello = self._request('hello', {})
        # Product versions are diagnostic. Both wire schemas must overlap.
        for field in ('management', 'machine_result'):
            limits = hello.get(field, {})
            low, high = limits.get('minimum'), limits.get('maximum')
            if (type(low) is not int or type(high) is not int or low < 1 or low > high):
                raise PlanError('invalid_input')
            if not low <= 1 <= high:
                raise PlanError('unsupported_version')
        if operation not in hello.get('capabilities', []):
            raise PlanError('unsupported_capability')
        return self._request(operation, arguments, negotiated=True)

    def _request(self, operation, arguments, negotiated=False):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=120)
        try:
            connection.request('POST', '/v1/management', json.dumps(dict(operation=operation, arguments=arguments)),
                               dict({'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
                                    **({'X-GitPickle-Management-Version': '1', 'X-GitPickle-Result-Version': '1'} if negotiated else {})))
            response = connection.getresponse()
            value = json.loads(response.read(8 * 1024 * 1024))
            if response.status != 200:
                raise PlanError(value.get('error', 'operational_failure'))
            return value['result']
        finally:
            connection.close()


def approve(message, args):
    if args.yes:
        return
    if args.json or not sys.stdin.isatty():
        raise PlanError('confirmation_required')
    print(message + ' [y/N] ', end='', file=sys.stderr, flush=True)
    if input().strip().lower() not in ('y', 'yes'):
        raise PlanError('confirmation_required')


def display(value):
    """Plain text, keyboard-only, bounded line width. No ANSI sequences."""
    import textwrap
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
    width = max(20, min(80, shutil.get_terminal_size().columns))
    return '\n'.join(line for raw in text.splitlines() for line in
                     (textwrap.wrap(raw, width=width, subsequent_indent='  ') or ['']))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PlanError('invalid_input')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    is_json = '--json' in argv
    accepted = []
    restore_job = None
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('command', choices=['protect', 'backup', 'sync', 'jobs', 'status', 'doctor', 'restore'])
        parser.add_argument('target', nargs='?')
        parser.add_argument('--server', default=os.environ.get('GITPICKLE_SERVER'))
        parser.add_argument('--repository')
        parser.add_argument('--all', action='store_true')
        parser.add_argument('--include-future', action='store_true')
        parser.add_argument('--preview', '--dry-run', action='store_true')
        parser.add_argument('--resume')
        parser.add_argument('--key')
        parser.add_argument('--wait', action='store_true')
        parser.add_argument('--yes', action='store_true')
        parser.add_argument('--json', action='store_true')
        parser.add_argument('--check', action='store_true')
        parser.add_argument('--attention', action='store_true')
        parser.add_argument('--destination')
        parser.add_argument('--restore', action='store_true', help='Inspect a restore job')
        args = parser.parse_args(argv)
        if args.restore and args.command != 'jobs':
            raise PlanError('invalid_input')
        if args.target is not None and args.command not in ('protect', 'jobs', 'restore'):
            raise PlanError('invalid_input')
        if args.resume and (args.command != 'protect' or args.repository or args.all or args.include_future):
            raise PlanError('invalid_input')
        if args.include_future and (args.command != 'protect' or not args.all):
            raise PlanError('invalid_input')
        if args.key and args.command not in ('backup', 'sync'):
            raise PlanError('invalid_input')
        if args.destination and args.command != 'restore':
            raise PlanError('invalid_input')
        if args.all and args.command in ('restore', 'jobs'):
            raise PlanError('invalid_input')
        if not args.server and args.command == 'protect' and sys.stdin.isatty() and not args.json:
            print('Run protection through an existing local daemon. Remote installation uses the deployment guide.', file=sys.stderr)
            print('Daemon URL (http://127.0.0.1:PORT): ', end='', file=sys.stderr, flush=True)
            args.server = input().strip()
        if not args.server:
            raise PlanError('configuration')
        token = os.environ.get('GITPICKLE_MANAGEMENT_TOKEN')
        if not token and sys.stdin.isatty() and not args.json:
            token = getpass.getpass('Management credential: ', stream=sys.stderr)
        if not token:
            raise PlanError('authentication')
        client = Client(args.server, token)
        if args.command == 'protect':
            print('Protection runs on the daemon at ' + args.server + '.', file=sys.stderr)
        repository = args.repository
        alias = checkout_alias() if not repository and not args.all and not args.resume and args.command not in ('jobs', 'restore') and not (args.command == 'protect' and args.target == 'github') else None
        # Resolve checkout only against authorized inventory. Never change a remote.
        if alias:
            if args.command == 'protect':
                inventory = client.call('workflow_inventory', {})
                matches = ['github-' + i for i, row in inventory.items() if row['alias'] == alias]
                if len(matches) != 1:
                    raise PlanError('invalid_scope')
                repository = matches[0]
            else:
                try:
                    repository = client.call('workflow_resolve', {'alias': alias})
                except PlanError as error:
                    if args.command not in ('status', 'doctor') or error.code != 'invalid_scope':
                        raise
        if args.all and repository:
            raise PlanError('invalid_input')
        if args.command == 'protect':
            if not args.target and not repository and not args.all and not args.resume:
                raise PlanError('invalid_scope')
            if args.target not in (None, 'github'):
                raise PlanError('invalid_input')
            if args.resume:
                preview = client.call('protect_resume', {'reference': args.resume})
            else:
                if not repository and not args.all:
                    inventory = client.call('workflow_inventory', {})
                    if args.json or not sys.stdin.isatty():
                        raise PlanError('explicit_scope_required')
                    print(str(len(inventory)) + ' authorized repositories found.', file=sys.stderr)
                    approve('Protect these repositories? Future repositories require --include-future.', args)
                    args.all = True
                if repository and not re.fullmatch(r'github-[1-9][0-9]*', repository):
                    raise PlanError('invalid_scope')
                preview = client.call('protect_preview', dict(repositories=[int(repository[7:])] if repository else None,
                    all=args.all, include_future=args.include_future))
            print('Plan (UTC):\n' + display(preview) + '\nCapture every 6 hours; discover daily.\nKeep all recovery points. Hosting is off.\nGit history, required LFS, enabled wikis.\nUncommitted work and unpushed branches are not included.\nNo local remotes change. Transfer charges and LFS size are unknown.', file=sys.stderr)
            if args.preview:
                result = envelope(preview, ['github-' + i for i in preview['plan']['preview']['plan']['repositories']])
            else:
                approve('Apply this protection plan?', args)
                data = client.call('protect_apply', dict(reference=preview['reference'], digest=preview['digest']))
                accepted = data['jobs']
                result = envelope(data, data['repositories'], jobs=accepted)
        elif args.command in ('backup', 'sync'):
            if not repository and not args.all:
                raise PlanError('explicit_scope_required')
            # Persist the client key before submission by requiring one in automation.
            key = args.key
            if not key:
                if args.json or not sys.stdin.isatty():
                    raise PlanError('idempotency_key_required')
                key = uuid.uuid4().hex
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
                raise PlanError('invalid_input')
            print('Scope: ' + ('all protected repositories' if args.all else repository), file=sys.stderr)
            print('Request key: ' + key, file=sys.stderr)
            preview = client.call('capture_preview', dict(repository=repository, all=args.all, operation=args.command, key=key))
            print('Capture plan (UTC):\n' + display(preview), file=sys.stderr)
            if args.preview:
                result = envelope(preview, preview['plan']['repositories'])
            else:
                approve('Apply this ' + args.command + ' request?', args)
                data = client.call('capture_accept', dict(reference=preview['reference'], digest=preview['digest']))
                accepted = data['jobs']
                result = envelope(data, data['repositories'], jobs=accepted)
        elif args.command == 'jobs':
            if not args.target:
                raise PlanError('invalid_input')
            if args.restore:
                data = client.call('job_get', {'job': args.target})
                result = envelope({'state': data, 'restore_job': args.target})
                result['job_id'] = args.target
                if args.wait:
                    print('Following restore. Ctrl-C detaches; server work continues.', file=sys.stderr)
                    while data == 'queued':
                        time.sleep(0.1)
                        data = client.call('job_get', {'job': args.target})
                    if data != 'completed':
                        raise PlanError('restore_failed')
                    result['data']['state'] = data
            else:
                data = client.call('capture_get', {'job': args.target})
                accepted = [data]
                result = envelope(data)
        elif args.command in ('status', 'doctor'):
            result = client.call('workflow_doctor' if args.command == 'doctor' else 'workflow_status', dict(repository=repository, all=args.all or not repository))
            if not args.json:
                print(status.render(result, args.attention, args.command == 'doctor'))
            else:
                print(json.dumps(result, sort_keys=True))
            return 6 if (args.check or args.command == 'doctor') and not result['data']['healthy'] else 0
        else:
            repository = repository or args.target
            if not repository:
                if args.json or not sys.stdin.isatty():
                    raise PlanError('explicit_scope_required')
                rows = client.call('restore_inventory', {})
                if not rows:
                    raise PlanError('not_found')
                for number, row in enumerate(rows, 1):
                    print(str(number) + ': ' + display(row), file=sys.stderr)
                print('Repository number: ', end='', file=sys.stderr, flush=True)
                number = input().strip()
                if not number.isdecimal() or not 1 <= int(number) <= len(rows):
                    raise PlanError('invalid_input')
                repository = rows[int(number) - 1]['repository_id']
            if not args.destination:
                if args.json or not sys.stdin.isatty():
                    raise PlanError('invalid_input')
                from datetime import datetime, timezone
                default = repository + '-restored-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                print('New destination [' + default + ']: ', end='', file=sys.stderr, flush=True)
                args.destination = input().strip() or default
            expires = int(time.time()) + 3600
            planned = client.call('restore_plan', dict(repository=repository,
                destination=str(Path(args.destination).absolute()), expires_at=expires, maximum_bytes=2**63-1))
            print('Restore plan (UTC):\n' + display(planned), file=sys.stderr)
            if args.preview:
                result = envelope(planned, [repository])
            else:
                approve('Restore into this new directory?', args)
                approval = client.call('approve_plan', dict(reference=planned['reference'], digest=planned['plan']['digest']))
                job = client.call('plan_apply', dict(plan=planned['plan'], approval=approval))
                restore_job = job
                print('Durable restore job: ' + job + '. Inspect with gitpickle jobs ' + job + ' --restore.', file=sys.stderr)
                result = envelope(dict(restore_job=job), [repository])
                result['job_id'] = job
                result['outcome'] = 'queued'
                if args.wait:
                    print('Following restore. Ctrl-C detaches; server work continues.', file=sys.stderr)
                    while True:
                        state = client.call('job_get', {'job': job})
                        if state != 'queued':
                            if state != 'completed':
                                raise PlanError('restore_failed')
                            result['outcome'] = 'ok'
                            break
                        time.sleep(0.1)
        if accepted:
            for job in accepted:
                print('Durable job: ' + job['id'], file=sys.stderr)
        if args.wait and accepted:
            print('Following jobs. Ctrl-C detaches; server work continues.', file=sys.stderr)
            while True:
                jobs = [client.call('capture_get', {'job': j['id']}) for j in accepted]
                if all(j['state'] in ('completed', 'failed', 'cancelled') for j in jobs):
                    result['outcome'] = 'failed' if any(j['state'] != 'completed' for j in jobs) else 'ok'
                    if result['outcome'] == 'failed':
                        result = envelope(repositories=result['scope']['repository_ids'], error='job_failed')
                        result['job_id'] = jobs[0]['id']
                    else:
                        result['data'] = dict(result['data'], jobs=jobs)
                    break
                time.sleep(0.1)
        print(json.dumps(result, sort_keys=True) if args.json else display(result))
        return 5 if result['error'] else 0
    except KeyboardInterrupt:
        print('Client detached. Server work continues. Use gitpickle jobs JOB_ID.', file=sys.stderr)
        result = envelope({'state': 'detached', 'jobs': accepted, 'restore_job': restore_job})
        print(json.dumps(result, sort_keys=True) if is_json else display(result))
        return 0
    except Exception as error:
        code = error.code if isinstance(error, (PlanError, JobError)) else 'operational_failure'
        result = envelope(error=code)
        print(code + ': Check the scope, runtime credentials, and server configuration.', file=sys.stderr)
        if is_json:
            print(json.dumps(result, sort_keys=True))
        return 3 if code in ('authentication', 'forbidden') else 2


if __name__ == '__main__':
    sys.exit(main())
