"""Transport-independent authorization service for the local archive adapter.

Principals and PolicyStore come from trusted server composition, never tool JSON.
Only this service exposes restore application to authenticated clients. The raw
recovery adapter remains available for operator-owned offline recovery.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import copy
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

import gitpickle_archive as a
import gitpickle_recovery as recovery
from gitpickle_writer import Writer


class PlanError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Principal:
    actor: str
    agent: bool = False

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.actor):
            raise PlanError('authentication')


@dataclass(frozen=True)
class Grant:
    actor: str
    permission: str
    repositories: frozenset
    expires_at: int
    sources: frozenset = frozenset()

    def __post_init__(self):
        if not self.sources <= {'github'}:
            raise PlanError('invalid_input')


@dataclass(frozen=True)
class Policy:
    revision: int
    grants: tuple
    approvers: frozenset
    sandbox: str
    # Exact actor/destination pairs authorized by the operator, outside sandbox.
    destinations: frozenset = frozenset()


class PolicyStore:
    """Trusted policy loader. Persist configuration outside client-controlled data.

    Replacement and execution share a lock. Revisions must increase, including
    after restart; the daemon's configuration adapter owns that persistence.
    """
    def __init__(self, policy):
        self.lock = threading.RLock()
        self.current = policy

    def replace(self, policy):
        with self.lock:
            if policy.revision <= self.current.revision:
                raise PlanError('conflict')
            self.current = policy


def boundary(name):
    """Fault boundary for PlanApproval model acceptance tests."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def plan_digest(plan):
    return a.digest(canonical({k: v for k, v in plan.items() if k != 'digest'}))


class Plans:
    def __init__(self, root, policies, *, clock=time.time, writer=None):
        self.root = Path(root).resolve()
        self.policies, self.clock = policies, clock
        self.writer = writer
        self.owns_writer = writer is None

    def __enter__(self):
        if self.owns_writer:
            self.writer = Writer(self.root).__enter__()
        if not self.writer.writable or self.writer.root != self.root:
            if self.owns_writer:
                self.writer.__exit__(None, None, None)
            raise PlanError('forbidden')
        self.archive = a.read(self.root, 'archive.json', 'archive')['archive_id']
        return self

    def __exit__(self, *_):
        if self.owns_writer:
            self.writer.__exit__(None, None, None)

    @contextmanager
    def _transaction(self):
        # A tuple key cannot collide with a real repository ID.
        with self.policies.lock, self.writer.mutation(('execution', 'plans')):
            yield self.policies.current

    def granted_repositories(self, principal, permission, policy, candidates=()):
        """Expand trusted source grants only over concrete repository IDs."""
        granted = set()
        for grant in policy.grants:
            if (grant.actor == principal.actor and grant.permission == permission
                    and self.clock() < grant.expires_at):
                granted.update(grant.repositories)
                if 'github' in grant.sources:
                    granted.update(repo for repo in candidates
                                   if re.fullmatch(r'github-[1-9][0-9]*', repo))
        return granted

    def require_source(self, principal, permission, source, policy):
        if not any(grant.actor == principal.actor and grant.permission == permission
                   and self.clock() < grant.expires_at and source in grant.sources
                   for grant in policy.grants):
            raise PlanError('forbidden')

    def _require(self, principal, permission, repositories, policy):
        granted = self.granted_repositories(principal, permission, policy, repositories)
        if not repositories or not set(repositories) <= granted:
            raise PlanError('forbidden')

    def _record(self, category, value):
        return a.record(self.root, 'execution/' + category,
                        dict(value, kind='execution', archive_id=self.archive))

    def _read(self, reference, category):
        prefix = 'execution/' + category + '/'
        if (not isinstance(reference, str) or not reference.startswith(prefix)
                or not re.fullmatch('[0-9a-f]{64}', reference[len(prefix):])):
            raise PlanError('approval_required')
        try:
            value = a.read(self.root, reference, 'execution')
        except FileNotFoundError:
            raise PlanError('approval_required') from None
        if value['archive_id'] != self.archive:
            raise PlanError('conflict')
        return value

    def _audit(self, principal, operation, result, plan=None):
        # No destinations, source text, URLs, exception messages, or credentials.
        self._record('audit', dict(actor=principal.actor, operation=operation,
            repositories=plan['repositories'] if plan else [],
            plan_digest=plan['digest'] if plan else None,
            policy_revision=self.policies.current.revision, result=result,
            recorded_at=self.clock(), event_id=uuid.uuid4().hex))

    def _versions(self, repositories, policy):
        return dict(policy=policy.revision, repositories={
            repo: (chain[-1] if (chain := a.inventory(self.root, repo)) else None)
            for repo in repositories})

    @contextmanager
    def _destination(self, principal, destination, visibility, policy, expected_parent=None):
        path = Path(destination)
        if (not path.is_absolute() or str(path) != destination
                or not re.fullmatch('[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}', path.name)
                or '..' in path.parts or visibility != 'private'):
            raise PlanError('unsafe_destination')
        if principal.agent and not (str(path.parent) == policy.sandbox or
                (principal.actor, destination) in policy.destinations):
            raise PlanError('unsafe_destination')
        # Walk with openat and O_NOFOLLOW, then retain the parent descriptor
        # through installation. A renamed parent cannot redirect filesystem I/O.
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parent.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            stat = os.fstat(fd)
            if principal.agent and (stat.st_uid != os.geteuid() or stat.st_mode & 0o022):
                raise PlanError('unsafe_destination')
            identity = [stat.st_dev, stat.st_ino]
            if expected_parent is not None and identity != expected_parent:
                raise PlanError('unsafe_destination')
            try:
                os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise PlanError('unsafe_destination')
            yield identity, Path(f'/proc/{os.getpid()}/fd/{fd}') / path.name
        except OSError:
            raise PlanError('unsafe_destination') from None
        finally:
            os.close(fd)

    def restore_plan(self, principal, repository, destination, *, expires_at,
                     maximum_bytes, maximum_repositories=1, visibility='private', point=None):
        with self._transaction() as policy:
            try:
                self._require(principal, 'plan_restore', [repository], policy)
                if point is not None and (not isinstance(point, str) or len(point) > 4096):
                    raise PlanError('invalid_input')
                if (not re.fullmatch('[A-Za-z0-9_-]+', repository)
                        or type(expires_at) is not int or expires_at <= self.clock()
                        or type(maximum_bytes) is not int or maximum_bytes < 0
                        or type(maximum_repositories) is not int or maximum_repositories < 1):
                    raise PlanError('invalid_input')
                with self._destination(principal, destination, visibility, policy) as (parent, _):
                    selected = recovery.plan(self.root, repository, point=point, destination=destination)
                    # Return only recovery inputs. Archive source labels can contain
                    # credentials or repository-supplied instructions.
                    preview = {key: selected[key] for key in
                               ('point', 'bytes', 'complete', 'coverage', 'selection')}
                plan = dict(version=1, id=uuid.uuid4().hex, actor=principal.actor,
                    agent=principal.agent, archive_id=self.archive, operation='restore',
                    repositories=[repository], destination=destination, visibility=visibility,
                    expected_versions=self._versions([repository], policy),
                    expires_at=expires_at, maximum_bytes=maximum_bytes,
                    maximum_repositories=maximum_repositories, overwrite=False,
                    destination_parent=parent, recovery=preview)
                if preview['bytes'] > maximum_bytes:
                    raise PlanError('impact_limit')
                plan['digest'] = plan_digest(plan)
                reference = self._record('plans', {'plan': plan})
                self._audit(principal, 'restore_plan', 'planned', plan)
                return dict(plan=copy.deepcopy(plan), reference=reference)
            except PlanError as error:
                self._audit(principal, 'restore_plan', error.code)
                raise

    def _validate(self, principal, plan, policy):
        if plan['actor'] != principal.actor or plan['agent'] != principal.agent:
            raise PlanError('forbidden')
        for permission in ('plan_restore', 'apply_plan'):
            self._require(principal, permission, plan['repositories'], policy)
        if plan['digest'] != plan_digest(plan):
            raise PlanError('conflict')
        if self.clock() >= plan['expires_at']:
            raise PlanError('expired_plan')
        if plan['expected_versions'] != self._versions(plan['repositories'], policy):
            raise PlanError('conflict')
        if (len(plan['repositories']) > plan['maximum_repositories'] or plan['overwrite']
                or plan['operation'] != 'restore'):
            raise PlanError('impact_limit')
        selected = recovery.point_info(self.root, plan['recovery']['point'], plan['repositories'][0])
        if not selected['complete'] or selected['bytes'] > plan['maximum_bytes']:
            raise PlanError('impact_limit')

    def approve(self, approver, reference, digest):
        """Trusted CLI or policy adapter only. An agent cannot call this authority."""
        with self._transaction() as policy:
            try:
                if approver.agent or approver.actor not in policy.approvers:
                    raise PlanError('forbidden')
                plan = self._read(reference, 'plans')['plan']
                if digest != plan['digest']:
                    raise PlanError('conflict')
                self._validate(Principal(plan['actor'], plan['agent']), plan, policy)
                with self._destination(Principal(plan['actor'], plan['agent']), plan['destination'],
                        plan['visibility'], policy, plan['destination_parent']):
                    approval = self._record('approvals', dict(plan=plan, reference=reference,
                        approver=approver.actor, approved_at=self.clock()))
                self._audit(approver, 'approve_plan', 'approved', plan)
                return approval
            except PlanError as error:
                self._audit(approver, 'approve_plan', error.code)
                raise

    def _approved(self, principal, candidate, reference, policy):
        approval = self._read(reference, 'approvals')
        stored = self._read(approval['reference'], 'plans')['plan']
        if (not isinstance(candidate, dict) or canonical(candidate) != canonical(stored)
                or approval['plan'] != stored):
            raise PlanError('conflict')
        if approval['approver'] not in policy.approvers:
            raise PlanError('forbidden')
        self._validate(principal, stored, policy)
        return stored

    def _event(self, job, event):
        prefix = f'jobs/{job}/{event}'
        paths = list((self.root / 'execution' / prefix).glob('*'))
        if len(paths) > 1:
            raise PlanError('conflict')
        return self._read('execution/' + prefix + '/' + paths[0].name, prefix) if paths else None

    def plan_apply(self, principal, candidate, approval):
        with self._transaction() as policy:
            try:
                plan = self._approved(principal, candidate, approval, policy)
                job = a.digest(a.encode({'plan': plan['id']}))
                if self._event(job, 'accepted'):
                    raise PlanError('replayed_plan')
                with self._destination(principal, plan['destination'], plan['visibility'], policy,
                                       plan['destination_parent']):
                    boundary('before_accept')
                    self._record(f'jobs/{job}/accepted', dict(actor=principal.actor, plan=plan,
                        approval=approval, accepted_at=self.clock()))
                    boundary('after_accept')
                self._audit(principal, 'plan_apply', 'accepted', plan)
                return job
            except PlanError as error:
                self._audit(principal, 'plan_apply', error.code)
                raise

    def inspect(self, principal, job):
        if not isinstance(job, str) or not re.fullmatch('[0-9a-f]{64}', job):
            raise PlanError('invalid_input')
        with self._transaction() as policy:
            accepted = self._event(job, 'accepted')
            if not accepted or accepted['actor'] != principal.actor:
                raise PlanError('forbidden')
            self._require(principal, 'plan_restore', accepted['plan']['repositories'], policy)
            terminal = self._event(job, 'terminal')
            return (terminal['result'] if terminal else
                    ('unknown_outcome' if self._event(job, 'started') else 'queued'))

    def execute(self, principal, job):
        # Repository lock prevents a capture between version checks and restore.
        with self._transaction():
            try:
                if not isinstance(job, str) or not re.fullmatch('[0-9a-f]{64}', job):
                    raise PlanError('invalid_input')
                accepted = self._event(job, 'accepted')
                if not accepted or accepted['actor'] != principal.actor:
                    raise PlanError('forbidden')
            except PlanError as error:
                self._audit(principal, 'restore', error.code)
                raise
        # Match hosting mutations: policy authority precedes repository locking.
        with self._transaction() as policy, self.writer.mutation(accepted['plan']['repositories'][0]):
            try:
                plan = self._approved(principal, accepted['plan'], accepted['approval'], policy)
                if self._event(job, 'started'):
                    raise PlanError('replayed_plan')
                with self._destination(principal, plan['destination'], plan['visibility'], policy,
                                       plan['destination_parent']) as (_, pinned):
                    self._record(f'jobs/{job}/started', dict(actor=principal.actor, started_at=self.clock()))
                    boundary('after_consume')
                    self._audit(principal, 'restore', 'started', plan)
                    # Revalidation precedes all side effects. The policy and repository
                    # locks remain held, and filesystem I/O uses the pinned parent.
                    # Durable writes can outlast the approval window. Check
                    # time and grant expiry once more immediately before I/O.
                    for permission in ('plan_restore', 'apply_plan'):
                        self._require(principal, permission, plan['repositories'], policy)
                    if self.clock() >= plan['expires_at']:
                        raise PlanError('expired_plan')
                    try:
                        result = a.restore(self.root, plan['recovery']['point'], pinned, _pinned=True)
                    except Exception:
                        self._record(f'jobs/{job}/terminal', {'result': 'restore_failed'})
                        self._audit(principal, 'restore', 'restore_failed', plan)
                        raise PlanError('restore_failed') from None
                    boundary('after_restore')
                    self._record(f'jobs/{job}/terminal', {'result': 'completed'})
                    self._audit(principal, 'restore', 'completed', plan)
                    return result
            except PlanError as error:
                self._audit(principal, 'restore', error.code)
                raise
