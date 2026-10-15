"""Durable repository modes and isolated native Git publication."""
import io
import os
from pathlib import Path
import tempfile

import gitpickle_archive as a
import gitpickle_security as security
from gitpickle_plans import PlanError
from gitpickle_receive import gate, packet


def state(root, repository):
    records, children = {}, {}
    prefix = f'execution/hosting/{repository}'
    for path in (Path(root) / prefix).glob('*'):
        key = prefix + '/' + path.name
        value = a.read(root, key, 'execution')
        if (value.get('archive_id') != a.read(root, 'archive.json', 'archive')['archive_id']
                or value.get('mode') not in ('hosted', 'protected_mirror')):
            raise ValueError('invalid repository mode')
        parent = value['parent']
        if parent in children:
            raise ValueError('conflicting repository modes')
        records[key], children[parent] = value, key
    key, seen = None, set()
    while key in children:
        key = children[key]
        if key in seen:
            raise ValueError('repository mode cycle')
        seen.add(key)
    if len(seen) != len(records):
        raise ValueError('disconnected repository modes')
    return (key, records[key]) if key else (None, dict(mode='protected_mirror'))


def head(root, repository):
    chain = a.inventory(root, repository)
    return chain[-1] if chain else None


def change(hosting, principal, repository, action, expected_head):
    plans = hosting.plans
    hosting.describe(principal, repository)
    with plans.policies.lock, plans.writer.mutation(repository):
        plans._require(principal, 'hosting_manage', [repository], plans.policies.current)
        parent, previous = state(plans.root, repository)
        current = head(plans.root, repository)
        if current != expected_head:
            raise PlanError('conflict')
        if action == 'create':
            if current is not None or parent is not None:
                raise PlanError('conflict')
            with tempfile.TemporaryDirectory(prefix=os.getenv('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-hosted-') as folder:
                a.git(folder, 'init', '--bare', '--template=', '--initial-branch=main')
                a._capture(plans.root, folder, repository, 'hosted:' + repository,
                           writer=plans.writer.owner_key)
            current = head(plans.root, repository)
        elif action == 'convert':
            if current is None or previous['mode'] != 'protected_mirror':
                raise PlanError('conflict')
            point = a.read(plans.root, current, 'publication')['point']
            import gitpickle_recovery as recovery
            if not recovery.point_info(plans.root, point, repository)['complete']:
                raise PlanError('generation_unavailable')
        elif action == 'resume':
            if previous['mode'] != 'hosted' or current is None:
                raise PlanError('conflict')
        else:
            raise PlanError('invalid_input')
        plans._require(principal, 'hosting_manage', [repository], plans.policies.current)
        key = a.record(plans.root, f'execution/hosting/{repository}', dict(
            kind='execution', parent=parent, archive_id=plans.archive,
            mode='protected_mirror' if action == 'resume' else 'hosted',
            checkpoint=current, actor=principal.actor))
        return dict(mode=state(plans.root, repository)[1]['mode'], checkpoint=current, record=key)


def receive(hosting, principal, repository, request):
    plans = hosting.plans
    with plans.policies.lock, plans.writer.mutation(repository):
        plans._require(principal, 'git_write', [repository], plans.policies.current)
        if state(plans.root, repository)[1]['mode'] != 'hosted':
            raise PlanError('forbidden')
        # Bind retries to the exact Git request. A committed operation wins even
        # when the original final response was lost. Native Git still owns refs.
        operation = 'push-' + a.digest(request)
        source = 'hosted:' + repository
        response_prefix = f'execution/pushes/{repository}/{operation}'
        base = head(plans.root, repository)
        responses = list((plans.root / response_prefix).glob('*'))
        if len(responses) > 1:
            raise ValueError('conflicting push responses')
        receipt = (a.read(plans.root, response_prefix + '/' + responses[0].name, 'execution')
                   if responses else None)
        committed = a.reconcile_capture(plans.root, repository, source, operation)
        if committed:
            current = a.read(plans.root, head(plans.root, repository), 'publication')['point']
            if current != committed:
                raise PlanError('conflict')
            if receipt is None:
                raise ValueError('missing committed push response')
            return bytes.fromhex(receipt['response'])
        if receipt is not None and receipt['base'] != base:
            raise PlanError('conflict')
        with hosting.generation(principal, repository) as (repo, previous):
            result = security.run(['git', *security.GIT_OPTIONS, 'receive-pack',
                                   '--stateless-rpc', str(repo)], input=request,
                                  env=security.git_environment(), timeout=120)
            final = gate(io.BytesIO(result.stdout), lambda data: None)
            for oid, size in a.required_lfs(repo).items():
                try:
                    with a.verified_file(plans.root, f'repos/{repository}/lfs/{oid}', size):
                        pass
                except FileNotFoundError:
                    raise PlanError('required_lfs_missing') from None
            plans._require(principal, 'git_write', [repository], plans.policies.current)
            if receipt is not None and receipt['response'] != final.hex():
                raise PlanError('conflict')
            a.record(plans.root, response_prefix, dict(kind='execution', base=base, response=final.hex()))
            a._capture(plans.root, repo, repository, source,
                       writer=plans.writer.owner_key, operation_id=operation, wiki=previous.get('wiki'))
            return final


def advertise(repo):
    result = security.run(['git', *security.GIT_OPTIONS, 'receive-pack',
                           '--stateless-rpc', '--advertise-refs', str(repo)],
                          env=security.git_environment(), timeout=120)
    return packet(b'# service=git-receive-pack\n') + b'0000' + result.stdout
