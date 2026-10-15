"""Synthetic usability rehearsal. This does not collect participant evidence."""
import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import time
from unittest.mock import patch

import gitpickle_archive as a
from gitpickle_plans import Grant, Policy
import gitpickle_status as status
import gitpickle_recovery as recovery
import test_gitpickle_workflow as fixture


def prepare(test, mode):
    count = 2 if mode == 'onboarding' else 404
    ids = frozenset(f'github-{i}' for i in range(1, count + 1))
    test.policies.replace(Policy(2, tuple(Grant('owner', p, ids, 2**62)
        for p in ('protect', 'status', 'backup', 'sync', 'plan_restore', 'apply_plan')),
        frozenset({'owner'}), str(test.root)))
    test.rows = {str(i): dict(id=i, alias=f'alice/project-{i}', has_wiki=False)
                 for i in range(1, count + 1)}
    if mode == 'portfolio':
        now = time.time()
        rows = {key: dict(repository_id=f'github-{key}', protected=True, available=True,
                observed_at=now, aliases=[row['alias']]) for key, row in test.rows.items()}
        a.record(test.archive, 'protection/github', dict(kind='protection', archive_id='workflow-test',
            parent=None, value=dict(repositories=rows, error=None, policy=None, discovery_due=0)))
        test.plans.writer.capture(test.source, 'github-1', 'github')
        old = datetime.fromtimestamp(now - 9 * 3600, timezone.utc).isoformat().replace('+00:00', 'Z')
        with patch.object(a, 'timestamp', return_value=old):
            test.plans.writer.capture(test.source, 'github-3', 'github')
        payload = b'unavailable required LFS object'
        oid = a.digest(payload)
        (test.source / 'large').write_text(
            f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n')
        a.git(test.source, 'add', 'large')
        a.git(test.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
              'commit', '-m', 'Add unavailable LFS pointer')
        test.plans.writer.capture(test.source, 'github-2', 'github')
    manifest = dict(schema_version=1, evidence_kind='synthetic-fixture', mode=mode,
        root=str(test.root), archive=str(test.archive), server=test.url,
        known_account_count=count + 1, authorized_count=count,
        hidden_repository=f'alice/project-{count + 1}',
        new_destination=str(test.root / 'recovered-new'),
        expected={'github-1': 'complete verified fresh', 'github-2': 'missing required LFS',
                  'github-3': 'verified but stale', 'github-404': 'configured without capture'}
                 if mode == 'portfolio' else {'scope': '2 authorized; 3 known in account'})
    path = test.root / 'moderator.json'
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    env = test.root / 'client.env'
    env.write_text('\n'.join(f'export {key}={shlex.quote(value)}' for key, value in {
        'GITPICKLE_SERVER': test.url, 'GITPICKLE_MANAGEMENT_TOKEN': test.token,
        'STUDY_ARCHIVE': str(test.archive), 'STUDY_DESTINATION': manifest['new_destination'],
        'NO_COLOR': '1'}.items()) + '\n')
    env.chmod(0o600)
    return manifest


def check(test, mode):
    if mode == 'onboarding':
        preview = test.preview(all=True)
        assert len(test.rows) == 2
        assert not preview['plan']['checks']['bucket_creation']
    else:
        rows = test.client.call('portfolio', {})
        assert len(rows) == 404
        result = status.calculate(test.archive)
        gaps = result['coverage_gaps']
        assert any(g['repository_id'] == 'github-2' and g['code'] == 'incomplete_coverage' for g in gaps)
        assert any(g['repository_id'] == 'github-3' and g['code'] == 'stale_coverage' for g in gaps)
        code, output, _ = test.command(['restore', 'github-1', '--destination',
            str(test.root / 'recovered-new'), '--preview', '--json'])
        assert code == 0, output
        assert not (test.root / 'recovered-new').exists()
        sentinel = test.root / 'existing-work'
        sentinel.mkdir()
        (sentinel / 'sentinel').write_bytes(b'keep')
        code, output, _ = test.command(['restore', 'github-1', '--destination',
            str(test.root / 'recovered-new'), '--yes', '--wait', '--json'])
        assert code == 0, output
        restored = test.root / 'recovered-new'
        a.git(restored, 'fsck', '--full')
        assert a.git(restored, 'show', 'refs/heads/main:committed') == b'data'
        # Keep the new-destination invariant for a hands-on rehearsal.
        shutil.rmtree(restored)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = recovery.main(['--from-storage', str(test.archive), '--repository', 'github-1',
                '--destination', str(sentinel), '--json'])
        assert code != 0
        assert (sentinel / 'sentinel').read_bytes() == b'keep'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('onboarding', 'portfolio'), required=True)
    parser.add_argument('--check', action='store_true', help='Check fixture invariants, then clean up')
    args = parser.parse_args()
    if not os.environ.get('LOOPER_RESOURCE_PREFIX'):
        parser.error('Set LOOPER_RESOURCE_PREFIX to a private rehearsal identifier')
    test = fixture.WorkflowTests()
    try:
        test.setUp()
        prepare(test, args.mode)
        check(test, args.mode)
        if args.check and args.mode == 'portfolio':
            test.listener.shutdown()
            test.listener.server_close()
            code, _, _ = test.command(['status', '--json'])
            assert code != 0
            destination = test.root / 'offline-restored'
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = recovery.main(['--from-storage', str(test.archive), '--repository', 'github-1',
                    '--destination', str(destination), '--json'])
            assert code == 0
            a.git(destination, 'fsck', '--full')
            assert a.git(destination, 'show', 'refs/heads/main:committed') == b'data'
        print(json.dumps(dict(evidence_kind='synthetic-rehearsal', checks='passed',
            moderator_manifest=str(test.root / 'moderator.json'),
            client_environment=str(test.root / 'client.env'))), flush=True)
        if not args.check:
            print('Moderator: type offline to stop this listener; type exit to clean up.', flush=True)
            for line in iter(input, 'exit'):
                if line == 'offline':
                    test.listener.shutdown()
                    test.listener.server_close()
                    print('Listener stopped. Archive remains until exit.', flush=True)
    except EOFError:
        pass
    finally:
        test.doCleanups()


if __name__ == '__main__':
    main()
