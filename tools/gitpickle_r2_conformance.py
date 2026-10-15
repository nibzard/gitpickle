"""Run the Rust storage harness in an owned R2 prefix, with recoverable cleanup."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

BASE = 'gitpickle-development/'
BUCKET = 'gitpickle-dev-tests'
ENDPOINT = 'https://24f97aae6afe0315dac069a5c08fe795.r2.cloudflarestorage.com'


def save(path, value):
    with Path(path).open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def client():
    import boto3
    from botocore.config import Config
    config = json.loads(os.environ['GITPICKLE_STORAGE_CONFIG'])
    assert config == dict(provider='r2', endpoint=ENDPOINT, region='auto', bucket=BUCKET, prefix=BASE)
    return boto3.client('s3', endpoint_url=ENDPOINT, region_name='auto',
                        aws_access_key_id=os.environ['GITPICKLE_STORAGE_ACCESS_KEY'],
                        aws_secret_access_key=os.environ['GITPICKLE_STORAGE_SECRET_KEY'],
                        config=Config(signature_version='s3v4', retries={'max_attempts': 0},
                                      connect_timeout=15, read_timeout=45,
                                      s3={'addressing_style': 'path'}))


def validate(state, prefix):
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,128}', prefix)
    assert state['resource_prefix'] == prefix
    assert re.fullmatch(r't007-[a-f0-9]{24}', state['run'])
    assert state['object_prefix'] == BASE + prefix + '/conformance/' + state['run'] + '/'
    assert state['bucket'] == BUCKET and state['endpoint'] == ENDPOINT
    assert state['uid'] == os.getuid()


def remove_owned(storage, state, events):
    """Delete exact journaled objects only after scope and content ownership checks."""
    from botocore.exceptions import ClientError
    hashes = {}
    initiated = set()
    ids = set()
    for event in events:
        key = event['key']
        if event['event'] == 'intent' and event['method'] != 'GET':
            assert key.startswith(state['object_prefix']) and '..' not in key.split('/')
            query = event['query']
            if event['method'] == 'PUT' and 'uploadId' not in query:
                hashes.setdefault(key, set()).add(event['sha256'])
            if event.get('object_sha256'):
                hashes.setdefault(key, set()).add(event['object_sha256'])
            if event['method'] == 'POST' and 'uploads' in query:
                initiated.add(key)
        if event['event'] == 'response' and event.get('upload_id'):
            ids.add((key, event['upload_id']))
    # A genuinely lost initiation response has no recorded ID. Restrict its
    # cleanup to an exact journaled key and this operation's creation interval.
    for page in storage.get_paginator('list_multipart_uploads').paginate(
            Bucket=BUCKET, Prefix=state['object_prefix']):
        for upload in page.get('Uploads', []):
            key, identifier = upload['Key'], upload['UploadId']
            assert key in initiated, 'Unowned multipart key; retain for recovery'
            assert ((key, identifier) in ids or
                    upload['Initiated'] >= datetime.fromisoformat(state['started'])), 'Upload predates operation'
            storage.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=identifier)
    for key, expected in hashes.items():
        try:
            obj = storage.get_object(Bucket=BUCKET, Key=key)
        except ClientError as error:
            if error.response['Error']['Code'] in ('NoSuchKey', '404'):
                continue
            raise
        assert hashlib.sha256(obj['Body'].read()).hexdigest() in expected, 'Object content changed; retain for recovery'
        storage.delete_object(Bucket=BUCKET, Key=key)
    # Listing is scoped and read-only. Unknown objects cause failure, never deletion.
    assert not storage.list_objects_v2(Bucket=BUCKET, Prefix=state['object_prefix']).get('Contents')


def cleanup(path, prefix):
    path = Path(path)
    assert not path.is_symlink() and path.stat().st_uid == os.getuid()
    state = json.loads(path.read_text())
    validate(state, prefix)
    journal = Path(state['journal'])
    assert journal.parent == path.parent and not journal.is_symlink() and journal.stat().st_uid == os.getuid()
    # A truncated journal is retained for operator recovery, never guessed.
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    remove_owned(client(), state, events)
    state['cleanup'] = 'owned objects and uploads absent'
    save(path, state)


def run():
    from gitpickle_dev_env import snapshot
    before = snapshot()
    artifact = Path(os.environ['LOOPER_ARTIFACT_DIR']).resolve()
    prefix = os.environ['LOOPER_RESOURCE_PREFIX']
    nonce = uuid.uuid4().hex[:24]
    stem = artifact / ('t007-' + nonce)
    receipt, journal, result = (str(stem) + suffix for suffix in ('.json', '.jsonl', '-result.json'))
    state = dict(schema_version=1, resource_prefix=prefix, run='t007-' + nonce,
                 object_prefix=BASE + prefix + '/conformance/t007-' + nonce + '/',
                 bucket=BUCKET, endpoint=ENDPOINT, uid=os.getuid(), journal=journal, result=result,
                 started=datetime.now(timezone.utc).isoformat())
    validate(state, prefix)
    storage = client()  # Validate the exact authorized configuration before setup.
    locks = subprocess.run(['cloudflare', 'r2', 'buckets', 'locks', 'get', BUCKET],
                           capture_output=True, text=True, timeout=60)
    assert locks.returncode == 0, 'Cannot inspect retention configuration before mutation'
    lock_rules = json.loads(locks.stdout)
    assert lock_rules == {'rules': []}, 'Disposable probes require an unlocked bucket'
    subprocess.run(['cargo', 'build', '--locked', '-p', 'gitpickle-adapters', '--example', 'r2_conformance'], check=True)
    assert not storage.list_objects_v2(Bucket=BUCKET, Prefix=state['object_prefix']).get('Contents')
    assert not storage.list_multipart_uploads(Bucket=BUCKET, Prefix=state['object_prefix']).get('Uploads')
    save(receipt, state)
    Path(journal).touch(mode=0o600, exist_ok=False)
    runner = str(stem) + '-cleanup.py'
    shutil.copyfile(__file__, runner)
    # Append and fsync before any external creation. Preserve setup registrations.
    with open(os.environ['LOOPER_RESOURCE_CLEANUP_FILE'], 'a') as stream:
        stream.write('\n# T007: only journaled objects and owned uploads; never delete the bucket\n')
        stream.write('(set +x; source /home/agent/.config/gitpickle/r2-test.env; python3 ' +
                     ' '.join(map(shlex.quote, [runner, 'cleanup', receipt, prefix])) + ')\n')
        stream.flush()
        os.fsync(stream.fileno())
    environment = dict(os.environ, GITPICKLE_R2_RECEIPT=receipt)
    completed = subprocess.run(['cargo', 'run', '--locked', '-p', 'gitpickle-adapters',
                                '--example', 'r2_conformance'], env=environment)
    report = json.loads(Path(result).read_text()) if Path(result).exists() else {'failure': 'Runner failed before report'}
    report.update(resource=state, timestamp=datetime.now(timezone.utc).isoformat())
    report['bucket_lock_rules_before'] = lock_rules
    events = [json.loads(line) for line in Path(journal).read_text().splitlines()]
    statuses = {}
    for event in events:
        if event['event'] == 'response':
            code = str(event['status'])
            statuses[code] = statuses.get(code, 0) + 1
    report['wire_evidence'] = {
        'journal_sha256': hashlib.sha256(Path(journal).read_bytes()).hexdigest(),
        'responses_by_status': statuses,
        'discarded_success_responses': sum(bool(e.get('dropped')) for e in events),
        'write_intents': [{'key': e['key'], 'sha256': e.get('object_sha256') or e['sha256']} for e in events
                           if e['event'] == 'intent' and
                           ((e['method'] == 'PUT' and 'uploadId' not in e['query']) or e.get('object_sha256'))]}
    report['git_revision'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    # Exercise recovery now and again. Looper keeps the same idempotent registration.
    cleanup(receipt, prefix)
    cleanup(receipt, prefix)
    report['cleanup'] = 'repeated cleanup passes; prefix has no objects or unfinished uploads'
    report['source_unchanged'] = snapshot() == before
    destination = Path('tests/results/storage/r2-conformance.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    save(destination, report)
    save(result, report)
    assert report['source_unchanged'], 'Verification changes project files'
    assert completed.returncode == 0 and 'failure' not in report, 'R2 conformance fails'
    print('R2 conformance passes: ' + str(destination))
    print('Retained evidence: ' + result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'cleanup'))
    parser.add_argument('receipt', nargs='?')
    parser.add_argument('prefix', nargs='?')
    args = parser.parse_args()
    try:
        if args.action == 'cleanup':
            cleanup(args.receipt, args.prefix)
        else:
            run()
    except Exception as error:
        print('T007 failed: ' + type(error).__name__ + '; provider diagnostics suppressed', file=sys.stderr)
        sys.exit(1)
