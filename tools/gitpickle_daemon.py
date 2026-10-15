"""Authenticated local management transport over the shared application services."""
import argparse
from contextlib import ExitStack
import threading
import uuid
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import signal
import sys

import gitpickle_security as security
import gitpickle_hosting as hosting
from gitpickle_plans import Grant, Plans, PlanError, Policy, PolicyStore, Principal
import gitpickle_status as status
from gitpickle_github import GitHub
from gitpickle_jobs import Jobs, JobError
from gitpickle_workflow import Workflow
from gitpickle_cloud import CloudArchive
import gitpickle_archive as archive
from gitpickle_agent import AgentTools, SCHEMAS


class Management:
    """Trusted composition supplies identity. Client fields cannot select an actor."""
    def __init__(self, plans, tokens, workflow=None, git_hosting=None):
        self.hosting = git_hosting
        self.plans = plans
        self.workflow = workflow
        self.tokens = []
        for token, identity in tokens.items():
            if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
                raise ValueError('invalid management credential')
            self.tokens.append((hashlib.sha256(token.encode()).digest(),
                                Principal(identity['actor'], identity.get('agent', True))))
        if not self.tokens:
            raise ValueError('management credentials required')

    def authenticate(self, header):
        candidate = hashlib.sha256(header.removeprefix('Bearer ').encode()).digest()
        for expected, principal in self.tokens:
            if hmac.compare_digest(candidate, expected) and header.startswith('Bearer '):
                return principal
        raise PlanError('authentication')

    def call(self, principal, operation, arguments):
        if operation == 'hosting_change':
            if self.hosting is None or not isinstance(arguments, dict) or set(arguments) != {'repository', 'action', 'expected_head'}:
                raise PlanError('invalid_input')
            return hosting.hosted.change(self.hosting, principal, **arguments)
        if operation == 'hosting_status':
            if not isinstance(arguments, dict) or set(arguments) != {'repository'}:
                raise PlanError('invalid_input')
            if self.hosting is None:
                with self.plans.policies.lock:
                    self.plans._require(principal, 'status', [arguments['repository']], self.plans.policies.current)
                return dict(enabled=False, mode='disabled', remote_url=None)
            return dict(enabled=True, **self.hosting.describe(principal, arguments['repository']))
        if operation.startswith('agent_'):
            return AgentTools(self.plans, self.workflow).call(principal, operation[6:], arguments)
        if self.workflow is not None and operation in {
                'workflow_inventory', 'protect_preview', 'protect_resume', 'protect_apply',
                'capture_preview', 'capture_accept', 'capture_get', 'workflow_status', 'workflow_doctor',
                'workflow_resolve', 'restore_inventory', 'repository_jobs', 'portfolio'}:
            return self.workflow.call(principal, operation, arguments)
        # Exact schemas reject actor spoofing, approval booleans, and unused URLs.
        fields = {
            'status': {'repository'},
            'restore_plan': {'repository', 'destination', 'expires_at', 'maximum_bytes'},
            'approve_plan': {'reference', 'digest'},
            'plan_apply': {'plan', 'approval'},
            'job_get': {'job'}, 'job_execute': {'job'}}
        if operation == 'restore_plan' and isinstance(arguments, dict) and 'point' in arguments:
            fields['restore_plan'] = fields['restore_plan'] | {'point'}
        if operation not in fields or not isinstance(arguments, dict) or set(arguments) != fields[operation]:
            raise PlanError('invalid_input')
        if operation == 'status':
            with self.plans.policies.lock:
                self.plans._require(principal, 'status', [arguments['repository']], self.plans.policies.current)
                result = status.calculate(self.plans.root, arguments['repository'])
                # Only return derived coverage, never archive source labels or logs.
                return {'healthy': result['data']['healthy'],
                        'repository': arguments['repository']}
        if operation == 'restore_plan':
            return self.plans.restore_plan(principal, **arguments)
        if operation == 'approve_plan':
            return self.plans.approve(principal, **arguments)
        if operation == 'plan_apply':
            job = self.plans.plan_apply(principal, arguments['plan'], arguments['approval'])
            if self.workflow is not None:
                self.workflow.start_restore(principal, job)
            return job
        if operation == 'job_get':
            state = self.plans.inspect(principal, arguments['job'])
            if state == 'unknown_outcome' and self.workflow is not None:
                future = self.workflow.restore_futures.get(arguments['job'])
                if future is not None and not future.done():
                    return 'queued'
            return state
        if operation == 'job_execute':
            result = self.plans.execute(principal, arguments['job'])
            return {'complete': result['complete']}


def hello(workflow_enabled):
    return dict(software_version=os.environ.get('GITPICKLE_SOFTWARE_VERSION', 'development'),
                management=dict(minimum=1, maximum=1), machine_result=dict(minimum=1, maximum=1),
                capabilities=['hosting_status', 'hosting_change'] + ['agent_' + name for name in SCHEMAS] + ['status', 'restore_plan', 'approve_plan', 'plan_apply', 'job_get', 'job_execute'] +
                        (['workflow_inventory', 'protect_preview', 'protect_resume', 'protect_apply',
                          'capture_preview', 'capture_accept', 'capture_get', 'workflow_status',
                          'workflow_doctor', 'workflow_resolve', 'restore_inventory', 'repository_jobs', 'portfolio']
                         if workflow_enabled else []))


class Handler(BaseHTTPRequestHandler):
    server_version = 'GitPickle'
    sys_version = ''
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass  # Never log request targets, authorization headers, or bodies.

    def reply(self, code, value):
        body = json.dumps(value, sort_keys=True).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': 'invalid_request'})

    def do_GET(self):
        if self.path in ('/livez', '/readyz'):
            stopping = getattr(self.server, 'stopping', None)
            ready = stopping is None or not stopping.is_set()
            self.reply(200 if ready else 503, {'status': 'ready' if ready else 'stopping'})
            return
        hosting.handle(self)

    def do_PUT(self):
        hosting.handle(self)

    def do_POST(self):
        if self.path.startswith('/git/'):
            hosting.handle(self)
            return
        try:
            if len(self.headers.get_all('Authorization', [])) != 1:
                raise PlanError('authentication')
            principal = self.server.application.authenticate(self.headers['Authorization'])
            if self.path != '/v1/management' or self.headers.get('Origin') is not None:
                raise PlanError('invalid_input')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
                raise PlanError('invalid_input')
            length = int(lengths[0])
            if not 0 < length <= 65536:
                raise PlanError('invalid_input')
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict) or set(request) != {'operation', 'arguments'}:
                raise PlanError('invalid_input')
            if request['operation'] == 'hello':
                if request['arguments'] != {}:
                    raise PlanError('invalid_input')
                result = hello(self.server.application.workflow is not None)
            else:
                for header in ('X-GitPickle-Management-Version', 'X-GitPickle-Result-Version'):
                    if self.headers.get_all(header, []) != ['1']:
                        raise PlanError('unsupported_version')
                result = self.server.application.call(principal, request['operation'], request['arguments'])
            self.reply(200, {'result': result})
        except (PlanError, JobError) as error:
            # Fixed codes only. No raw exception text reaches a response.
            self.reply(401 if error.code == 'authentication' else 403,
                       {'error': error.code})
        except Exception:
            self.reply(400, {'error': 'request_failed'})


def server(application, host='127.0.0.1', port=0, *, container_network=False):
    if (not ipaddress.ip_address(host).is_loopback and not
            (container_network and host == '0.0.0.0')) or ':' in host:
        raise ValueError('management requires an IPv4 loopback address')
    listener = HTTPServer((host, port), Handler)
    listener.application = application
    return listener


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='Private archive state directory')
    parser.add_argument('--policy', required=True, help='Private nonsecret policy file')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--container-network', action='store_true',
                        help='Allow 0.0.0.0 inside an isolated container; publish only on host loopback')
    parser.add_argument('--enable-git', action='store_true', help='Explicitly enable Git hosting; mirrors remain read-only')
    parser.add_argument('--git-base-url', help='Canonical HTTPS URL: https://HOST/git')
    parser.add_argument('--staging-bytes', type=int, help='Enable protection with this per-job disk budget')
    args = parser.parse_args(argv)
    phase = 'private_paths'
    try:
        if os.geteuid() == 0:
            raise ValueError('daemon requires an unprivileged user')
        if bool(args.git_base_url) != args.enable_git:
            raise ValueError('enable Git explicitly with its canonical HTTPS URL')
        os.umask(0o077)
        root = security.private_directory(args.state)
        policy_path = Path(args.policy)
        if policy_path.is_symlink():
            raise ValueError('unsafe policy path')
        info = policy_path.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('policy file must be private')
        config = json.loads(policy_path.read_text())
        if set(config) != {'revision', 'grants', 'approvers', 'sandbox'}:
            raise ValueError('invalid policy schema')
        sandbox = security.private_directory(config['sandbox'])
        grants = tuple(Grant(g['actor'], g['permission'], frozenset(g['repositories']),
                             g['expires_at']) for g in config['grants'])
        policies = PolicyStore(Policy(config['revision'], grants, frozenset(config['approvers']), str(sandbox)))
        phase = 'runtime_credentials'
        tokens = json.loads(os.environ.pop('GITPICKLE_MANAGEMENT_TOKENS'))
        with ExitStack() as stack:
            phase = 'archive_reconstruction'
            cloud = None
            if os.environ.get('GITPICKLE_STORAGE_CONFIG'):
                cloud = stack.enter_context(CloudArchive(root, os.environ['GITPICKLE_EXECUTABLE']))
            elif not (root / 'archive.json').exists():
                archive.initialize(root, uuid.uuid4().hex)
            plans = stack.enter_context(Plans(root, policies))
            phase = 'application_services'
            git_hosting = hosting.Hosting(plans, args.git_base_url) if args.enable_git else None
            workflow = None
            if args.staging_bytes is not None:
                if args.staging_bytes <= 0:
                    raise ValueError('positive staging budget required')
                github_token = os.environ.pop('GITPICKLE_GITHUB_TOKEN', '')
                github = GitHub(lambda: github_token)
                jobs = stack.enter_context(Jobs(root, source_adapter=github, writer=plans.writer))
                workflow = Workflow(plans, jobs, github, args.staging_bytes, cloud=cloud)
                jobs.start()
                stopping = threading.Event()
                def schedule():
                    while not stopping.wait(0.25):
                        try:
                            with workflow.lock:
                                workflow.protection.tick(staging_bytes=args.staging_bytes)
                        except Exception:
                            # Failed probes never acknowledge new work. Retry after delay.
                            stopping.wait(5)
                scheduler = threading.Thread(target=schedule, daemon=True)
                scheduler.start()
                def stop_scheduler():
                    stopping.set()
                    scheduler.join()
                stack.callback(stop_scheduler)
            phase = 'listener'
            with server(Management(plans, tokens, workflow, git_hosting), args.host, args.port,
                        container_network=args.container_network) as listener:
                # Finish the current request, stop admission, then drain workers.
                # Do not interrupt an archive publication in a signal handler.
                draining = threading.Event()
                listener.stopping = draining
                listener.timeout = 0.25
                def stop(*_):
                    draining.set()
                signal.signal(signal.SIGTERM, stop)
                signal.signal(signal.SIGINT, stop)
                print(json.dumps({'listen': list(listener.server_address)}), flush=True)
                while not draining.is_set():
                    listener.handle_request()
                print(json.dumps({'event': 'draining'}), file=sys.stderr, flush=True)
        print(json.dumps({'event': 'stopped'}), file=sys.stderr, flush=True)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('configuration: Cannot start management service. Check private paths, policy, and runtime credentials. Phase: ' + phase,
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
