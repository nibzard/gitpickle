"""Bounded hosted publication and disk-loss recovery against authorized R2."""
import json
import os
from pathlib import Path
import shlex
import shutil
import time
import uuid
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_hosted as hosted
import gitpickle_perf as fixtures
import gitpickle_portfolio_recovery as acceptance
import gitpickle_test_resources as resources
from gitpickle_dev_env import snapshot
from gitpickle_hosting import Hosting
from gitpickle_jobs import JobError
from gitpickle_plans import Grant, PlanError, Plans, Policy, PolicyStore, Principal
from gitpickle_receive import packet


def run():
    before = snapshot()
    artifact = Path(os.environ['LOOPER_ARTIFACT_DIR']).resolve()
    prefix = os.environ['LOOPER_RESOURCE_PREFIX']
    # Reuse the T022 ownership receipt and its standalone cleanup implementation.
    run_id = 't022-' + uuid.uuid4().hex[:24]
    base = artifact / run_id
    base.mkdir(mode=0o700)
    receipt = artifact / (run_id + '.json')
    state = dict(resource_prefix=prefix, uid=os.getuid(), run=run_id,
        object_prefix=acceptance.BASE + prefix + '/recovery/' + run_id + '/',
        bucket=acceptance.BUCKET, endpoint=acceptance.ENDPOINT,
        journal=str(artifact / (run_id + '.jsonl')), cache=str(base / 'cache'),
        executable=str((Path(os.environ.get('CARGO_TARGET_DIR') or 'target') / 'debug' / 'gitpickle').resolve()))
    acceptance.validate(state, prefix)
    storage = resources.client()
    assert not storage.list_objects_v2(Bucket=state['bucket'], Prefix=state['object_prefix']).get('Contents')
    assert not storage.list_multipart_uploads(Bucket=state['bucket'], Prefix=state['object_prefix']).get('Uploads')
    acceptance.save(receipt, state)
    Path(state['journal']).touch(mode=0o600, exist_ok=False)
    runner = artifact / (run_id + '-cleanup.py')
    shutil.copyfile(acceptance.__file__, runner)
    with open(os.environ['LOOPER_RESOURCE_CLEANUP_FILE'], 'a') as stream:
        stream.write('\n# T052 hosted R2: exact journaled objects; preserve bucket\n')
        stream.write('(set +x; source /home/agent/.config/gitpickle/r2-test.env; python3 ' +
                     shlex.join([str(runner), 'cleanup', str(receipt), prefix]) + ')\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.environ['GITPICKLE_STORAGE_CONFIG'] = json.dumps(dict(provider='r2', endpoint=state['endpoint'],
        region='auto', bucket=state['bucket'], prefix=state['object_prefix']))
    import subprocess
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report = dict(schema_version=1, task_id='T052', status='incomplete', resource=state, checks={},
        commit=revision, source_snapshot_sha256=a.digest(json.dumps(before, sort_keys=True).encode()),
        limitations=['Bounded native receive-pack coordinator; HTTPS clients are tested locally.',
                     'No full-size, S3/B2, fresh-VM, or complete M4 acceptance claim.'])
    result = base / 'hosted-result.json'
    acceptance.save(result, report)
    start = time.monotonic()
    try:
        source = base / 'source.git'
        fixtures.make_repo(source, 52, 2, 1024)
        payload = b'T052 required LFS content\n' * 64
        oid = a.digest(payload)
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode()
        blob = a.git(source, 'hash-object', '-w', '--stdin', data=pointer).decode().strip()
        tree = a.git(source, 'mktree', data=f'100644 blob {blob}\tasset\n'.encode()).decode().strip()
        previous = a.git(source, 'rev-parse', 'main').decode().strip()
        tip = a.git(source, '-c', 'user.name=Acceptance', '-c', 'user.email=acceptance@example.invalid',
                    'commit-tree', tree, '-p', previous, data=b'LFS push\n').strip()
        a.git(source, 'update-ref', 'refs/heads/main', tip.decode())
        pack = a.git(source, 'pack-objects', '--stdout', '--all')
        request = packet(b'0' * 40 + b' ' + tip + b' refs/heads/main\0report-status side-band-64k\n') + b'0000' + pack
        policies = PolicyStore(Policy(1, tuple(Grant('owner', permission, frozenset({'repo'}),
            int(time.time()) + 3600) for permission in ('git_read', 'git_write', 'hosting_manage')),
            frozenset(), str(base)))
        principal = Principal('owner')
        with acceptance.cloud(state) as cloud, Plans(state['cache'], policies) as plans:
            hosting = Hosting(plans, 'https://example.invalid/git')
            hosted.change(hosting, principal, 'repo', 'create', None)
            initial = hosted.head(plans.root, 'repo')
            try:
                hosted.receive(hosting, principal, 'repo', request)
                raise AssertionError('Missing LFS accepted')
            except PlanError as error:
                assert str(error) == 'required_lfs_missing', type(error).__name__
            assert hosted.head(plans.root, 'repo') == initial
            a.durable_put(plans.root, f'repos/repo/lfs/{oid}', payload)
            command = cloud.command
            def fail(operation, *arguments):
                if operation == 'put' and '/packs/' in arguments[0]:
                    raise JobError('storage_unavailable')
                return command(operation, *arguments)
            with patch.object(cloud, 'command', fail):
                try:
                    hosted.receive(hosting, principal, 'repo', request)
                    raise AssertionError('Failed storage accepted')
                except JobError:
                    pass
            assert hosted.head(plans.root, 'repo') == initial
            response = hosted.receive(hosting, principal, 'repo', request)
            assert b'ok refs/heads/main' in response
            committed = hosted.head(plans.root, 'repo')
            point = a.read(plans.root, committed, 'publication')['point']
            report['checks']['publication'] = dict(head=committed, point=point,
                tip=tip.decode(), lfs_oid=oid, missing_lfs_rejected=True, failed_storage_rejected=True)
        shutil.rmtree(state['cache'])
        shutil.rmtree(source)
        with acceptance.cloud(state), Plans(state['cache'], policies) as plans:
            hosting = Hosting(plans, 'https://example.invalid/git')
            assert hosting.describe(principal, 'repo')['mode'] == 'hosted'
            assert hosted.receive(hosting, principal, 'repo', request) == response
            restored = a.restore(plans.root, point, base / 'restored.git')
            assert set(restored['verified_components']) == {'git', 'lfs'}
            assert a.git(base / 'restored.git', 'rev-parse', 'main').strip() == tip
            with a.verified_file(plans.root, f'repos/repo/lfs/{oid}', len(payload)) as stream:
                assert stream.read() == payload
            report['checks']['disk_loss'] = dict(source_absent=True, cache_rebuilt=True,
                mode_rebuilt=True, response_replayed=True, verified_components=restored['verified_components'])
        report['source_unchanged'] = snapshot() == before
        assert report['source_unchanged']
        report['status'] = 'passed'
    finally:
        acceptance.cleanup(receipt, prefix, storage)
        acceptance.cleanup(receipt, prefix, storage)
        report['cleanup'] = 'owned objects absent after repeated cleanup'
        report['elapsed_seconds'] = time.monotonic() - start
        acceptance.save(result, report)
        output = Path('tests/results/implementation/t052-hosted.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        acceptance.save(output, dict(report, retained_report=str(result)))
    print(result)


if __name__ == '__main__':
    run()
