"""Provision authorized T004 fixtures; never change the persistent R2 bucket."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

ACCOUNT = '24f97aae6afe0315dac069a5c08fe795'
BUCKET = 'gitpickle-dev-tests'
ENDPOINT = f'https://{ACCOUNT}.r2.cloudflarestorage.com'
BASE = 'gitpickle-development/'
OWNER = 'nibzard'
OWNER_ID = 6250945


def command(args, allow_missing=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if result.returncode:
        if allow_missing and '(HTTP 404)' in result.stderr:
            return None
        # Provider diagnostics can contain credentials. Emit only the command family.
        raise RuntimeError(f'{args[0]} failed with exit code {result.returncode}')
    return result.stdout


def api(path, allow_missing=False):
    raw = command(['gh', 'api', path], allow_missing)
    return None if raw is None else json.loads(raw)


def client():
    import boto3
    from botocore.config import Config
    config = json.loads(os.environ['GITPICKLE_STORAGE_CONFIG'])
    assert config == dict(provider='r2', endpoint=ENDPOINT, region='auto',
                          bucket=BUCKET, prefix=BASE), 'Unexpected storage scope'
    return boto3.client(
        's3', endpoint_url=ENDPOINT, region_name='auto',
        aws_access_key_id=os.environ['GITPICKLE_STORAGE_ACCESS_KEY'],
        aws_secret_access_key=os.environ['GITPICKLE_STORAGE_SECRET_KEY'],
        config=Config(signature_version='s3v4', retries={'max_attempts': 0},
                      connect_timeout=15, read_timeout=30,
                      s3={'addressing_style': 'path'}))


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def validate_state(state, prefix):
    assert re.fullmatch(r'[A-Za-z0-9_-]+', prefix), 'Invalid resource prefix'
    assert state['resource_prefix'] == prefix
    assert re.fullmatch(r'[a-f0-9]{12}', state['nonce'])
    assert state['name'] == 'gitpickle-' + prefix.lower().replace('_', '-') + '-' + state['nonce']
    assert state['object_prefix'] == BASE + prefix + '/t004-' + state['nonce'] + '/'
    assert state['key'] == state['object_prefix'] + 'probe.txt'
    assert state['marker'] == 'GitPickle disposable T004 ' + prefix + ' ' + state['nonce']


def remove_object(storage, state):
    from botocore.exceptions import ClientError
    try:
        obj = storage.get_object(Bucket=BUCKET, Key=state['key'])
    except ClientError as error:
        if error.response['Error']['Code'] in ('NoSuchKey', '404'):
            return
        raise
    body = obj['Body'].read()
    assert obj['Metadata'].get('gitpickle-owner') == state['marker'], 'Object ownership changed'
    assert hashlib.sha256(body).hexdigest() == state['body_sha256'], 'Object content changed'
    # Only this operation owns this random key. Never enumerate objects for deletion.
    storage.delete_object(Bucket=BUCKET, Key=state['key'])


def cleanup(path, prefix):
    assert not path.is_symlink() and path.stat().st_uid == os.getuid(), 'Invalid cleanup receipt'
    state = json.loads(path.read_text())
    validate_state(state, prefix)
    errors = []
    try:
        user = api('user')
        assert user['login'] == OWNER and user['id'] == OWNER_ID, 'GitHub identity changed'
        repo = api('repos/' + OWNER + '/' + state['name'], True)
        if repo is not None:
            assert repo['owner']['id'] == OWNER_ID and repo['private'], 'Repository owner changed'
            assert repo['name'] == state['name'] and repo['description'] == state['marker']
            assert repo['created_at'] >= state['started'], 'Repository predates this attempt'
            if state.get('repo_id'):
                assert repo['id'] == state['repo_id'], 'Repository identity changed'
            command(['gh', 'repo', 'delete', OWNER + '/' + state['name'], '--yes'])
        state['github_cleanup'] = 'absent'
    except Exception:
        errors.append('GitHub ownership or cleanup check failed; retain receipt for recovery')
    try:
        remove_object(client(), state)
        state['object_cleanup'] = 'absent'
    except Exception:
        errors.append('R2 ownership or cleanup check failed; retain receipt for recovery')
    save(path, state)
    if errors:
        raise RuntimeError('; '.join(errors))


def provision(report_path):
    from gitpickle_dev_env import snapshot
    before = snapshot()
    artifact = Path(os.environ['LOOPER_ARTIFACT_DIR']).resolve()
    prefix = os.environ['LOOPER_RESOURCE_PREFIX']
    nonce = uuid.uuid4().hex[:12]
    state = dict(resource_prefix=prefix, nonce=nonce,
                 name='gitpickle-' + prefix.lower().replace('_', '-') + '-' + nonce,
                 object_prefix=BASE + prefix + '/t004-' + nonce + '/',
                 marker='GitPickle disposable T004 ' + prefix + ' ' + nonce,
                 started=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    state['key'] = state['object_prefix'] + 'probe.txt'
    body = (state['marker'] + '\n').encode()
    state['body_sha256'] = hashlib.sha256(body).hexdigest()
    validate_state(state, prefix)
    report = dict(schema_version=1, task_id='T004', status='incomplete',
                  checked_at=state['started'], resource_prefix=prefix, checks={}, blockers=[])
    receipt = artifact / ('t004-ownership-' + nonce + '.json')
    try:
        identity = json.loads(command(['cloudflare', 'auth', 'whoami']))
        assert identity['authenticated'] and identity['tokenValid']
        assert any(account['id'] == ACCOUNT for account in identity['accounts'])
        os.environ['CLOUDFLARE_ACCOUNT_ID'] = ACCOUNT
        buckets = json.loads(command(['cloudflare', 'r2', 'buckets', 'list', '--name-contains', BUCKET]))
        assert any(bucket['name'] == BUCKET for bucket in buckets['buckets'])
        managed = json.loads(command(['cloudflare', 'r2', 'buckets', 'domains', 'managed', 'list', '--bucket-name', BUCKET]))
        custom = json.loads(command(['cloudflare', 'r2', 'buckets', 'domains', 'custom', 'list', '--bucket-name', BUCKET]))
        assert not managed['enabled'] and not custom['domains'], 'Bucket public access found'
        user = api('user')
        assert user['login'] == OWNER and user['id'] == OWNER_ID
        headers = command(['gh', 'api', '-i', 'user'])
        scopes = next(line.split(':', 1)[1].strip().split(', ') for line in headers.splitlines()
                      if line.lower().startswith('x-oauth-scopes:'))
        assert {'repo', 'delete_repo'} <= set(scopes), 'Missing GitHub fixture permissions'
        assert api('repos/' + OWNER + '/' + state['name'], True) is None
        storage = client()
        listing = storage.list_objects_v2(Bucket=BUCKET, Prefix=state['object_prefix'])
        assert not listing.get('Contents') and not listing.get('IsTruncated'), 'Prefix collision'
        report['checks']['preflight'] = dict(
            cloudflare_account_id=ACCOUNT, cloudflare_authenticated=True,
            github_login=OWNER, github_user_id=OWNER_ID, github_oauth_scopes=scopes,
            bucket_exists=True, public_managed_domain=False, custom_domains=[],
            unique_repository_absent=True, unique_object_prefix_empty=True)
        receipt.touch(mode=0o600, exist_ok=False)
        save(receipt, state)
        # Copy the runner outside the checkout. Recovery does not depend on this worktree.
        runner = artifact / ('t004-cleanup-' + nonce + '.py')
        runner.write_bytes(Path(__file__).read_bytes())
        runner.chmod(0o700)
        args = [sys.executable, str(runner), 'cleanup', str(receipt), prefix]
        # Append without replacing setup registrations. Source credentials only in this shell.
        line = ('\n# T004: owned fixture only; persistent R2 bucket is never deleted\n'
                '( set +x; source /home/agent/.config/gitpickle/r2-test.env && '
                + shlex.join(args) + ' )\n')
        with open(os.environ['LOOPER_RESOURCE_CLEANUP_FILE'], 'a') as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        report['cleanup'] = dict(registered_before_creation=True, receipt=str(receipt),
                                 runner=str(runner), file=os.environ['LOOPER_RESOURCE_CLEANUP_FILE'])
        command(['gh', 'repo', 'create', OWNER + '/' + state['name'], '--private',
                 '--description', state['marker'], '--disable-issues'])
        repo = api('repos/' + OWNER + '/' + state['name'])
        assert repo['private'] and repo['owner']['id'] == OWNER_ID and repo['permissions']['admin']
        assert repo['description'] == state['marker']
        state['repo_id'] = repo['id']
        save(receipt, state)
        report['github'] = dict(repository=repo['full_name'], id=repo['id'],
                                 private=True, permissions=repo['permissions'],
                                 has_wiki=repo['has_wiki'], branches=api('repos/' + repo['full_name'] + '/branches'),
                                 lifetime='disposable; Looper cleanup removes this fixture')
        storage.put_object(Bucket=BUCKET, Key=state['key'], Body=body,
                           Metadata={'gitpickle-owner': state['marker']})
        obj = storage.get_object(Bucket=BUCKET, Key=state['key'])
        assert obj['Body'].read() == body
        listing = storage.list_objects_v2(Bucket=BUCKET, Prefix=state['object_prefix'])
        assert [item['Key'] for item in listing.get('Contents', [])] == [state['key']]
        remove_object(storage, state)
        remove_object(storage, state)
        assert not storage.list_objects_v2(Bucket=BUCKET, Prefix=state['object_prefix']).get('Contents')
        state['object_cleanup'] = 'absent'
        save(receipt, state)
        report['r2'] = dict(account_id=ACCOUNT, endpoint=ENDPOINT, region='auto', bucket=BUCKET,
                             permitted_base_prefix=BASE, operation_prefix=state['object_prefix'],
                             key=state['key'], body_sha256=state['body_sha256'],
                             operations_verified=['signed_list', 'signed_write', 'readback', 'ownership_checked_delete', 'idempotent_delete'],
                             object_cleanup='absent', bucket_lifetime='owner-provided persistent; never delete',
                             credential_scope='Owner-provided runtime credentials; IAM scope not independently introspected',
                             conformance='not established; T007 remains required')
        report['status'] = 'passed'
    except Exception as error:
        # Avoid exception text from SDKs and providers: it can include signed requests.
        report['status'] = 'blocked'
        code = getattr(error, 'response', {}).get('Error', {}).get('Code')
        detail = str(error) if isinstance(error, (AssertionError, RuntimeError, KeyError)) else type(error).__name__
        report['blockers'].append('Provisioning failed: ' + (code or detail))
    finally:
        report['source_unchanged'] = before == snapshot()
        if not report['source_unchanged']:
            report['status'] = 'blocked'
            report['blockers'].append('Setup changed source files')
        report_path.parent.mkdir(parents=True, exist_ok=True)
        save(report_path, report)
        save(artifact / 't004-preflight.json', report)
        print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


def main():
    if sys.flags.optimize:
        raise RuntimeError('Run without Python optimization; ownership checks are required')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('provision')
    p.add_argument('--report', type=Path, default=Path('tests/results/resources/t004-preflight.json'))
    p = sub.add_parser('cleanup')
    p.add_argument('receipt', type=Path)
    p.add_argument('prefix')
    args = parser.parse_args()
    if args.action == 'cleanup':
        cleanup(args.receipt, args.prefix)
        print('T004 owned fixture resources are absent; persistent bucket preserved')
        return 0
    return provision(args.report)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('T004 failed: ' + type(error).__name__ + '; no provider diagnostics emitted', file=sys.stderr)
        sys.exit(1)
