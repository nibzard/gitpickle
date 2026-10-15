"""Bounded local and live R2 recovery acceptance with owned resource cleanup.

Run without Python optimization. Credentials stay in the runtime environment.
The cleanup branch is standalone so its retained copy survives worktree loss.
"""
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
import time
import uuid

BASE = 'gitpickle-development/'
BUCKET = 'gitpickle-dev-tests'
HOST = '24f97aae6afe0315dac069a5c08fe795.r2.cloudflarestorage.com'
ENDPOINT = 'https://' + HOST


def save(path, value):
    with Path(path).open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def validate(state, prefix):
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,128}', prefix)
    assert state['resource_prefix'] == prefix and state['uid'] == os.getuid()
    assert re.fullmatch(r't022-[a-f0-9]{24}', state['run'])
    assert state['object_prefix'] == BASE + prefix + '/recovery/' + state['run'] + '/'
    assert state['bucket'] == BUCKET and state['endpoint'] == ENDPOINT


def cleanup(receipt, prefix, storage=None):
    """Remove exact journaled keys after digest checks; retain unknown objects."""
    receipt = Path(receipt)
    assert not receipt.is_symlink() and receipt.stat().st_uid == os.getuid()
    state = json.loads(receipt.read_text())
    validate(state, prefix)
    journal = Path(state['journal'])
    assert journal.parent == receipt.parent and not journal.is_symlink()
    assert journal.stat().st_uid == os.getuid()
    expected = {}
    for line in journal.read_text().splitlines():
        event = json.loads(line)
        key = event['key']
        assert key.startswith(state['object_prefix']) and '..' not in key.split('/')
        assert re.fullmatch('[a-f0-9]{64}', event['sha256'])
        expected.setdefault(key, set()).add(event['sha256'])
    if storage is None:
        import boto3
        from botocore.config import Config
        config = json.loads(os.environ['GITPICKLE_STORAGE_CONFIG'])
        assert config == dict(provider='r2', endpoint=ENDPOINT, region='auto', bucket=BUCKET, prefix=BASE)
        storage = boto3.client('s3', endpoint_url=ENDPOINT, region_name='auto',
            aws_access_key_id=os.environ['GITPICKLE_STORAGE_ACCESS_KEY'],
            aws_secret_access_key=os.environ['GITPICKLE_STORAGE_SECRET_KEY'],
            config=Config(signature_version='s3v4', connect_timeout=15, read_timeout=45,
                          s3={'addressing_style': 'path'}))
    from botocore.exceptions import ClientError
    for key, hashes in expected.items():
        try:
            obj = storage.get_object(Bucket=BUCKET, Key=key)
        except ClientError as error:
            if error.response['Error']['Code'] in ('404', 'NoSuchKey'):
                continue
            raise
        assert hashlib.sha256(obj['Body'].read()).hexdigest() in hashes, 'Changed object retained'
        storage.delete_object(Bucket=BUCKET, Key=key)
    remaining = storage.list_objects_v2(Bucket=BUCKET, Prefix=state['object_prefix'])
    uploads = storage.list_multipart_uploads(Bucket=BUCKET, Prefix=state['object_prefix'])
    assert not remaining.get('Contents') and not remaining.get('IsTruncated')
    assert not uploads.get('Uploads') and not uploads.get('IsTruncated')
    state['cleanup'] = 'owned objects absent; bucket preserved'
    save(receipt, state)


def cloud(state, crash=False):
    from gitpickle_cloud import CloudArchive
    from gitpickle_security import private_directory
    private_directory(state['cache'])
    class JournaledArchive(CloudArchive):
        def _command(self, operation, *args):
            if operation == 'put':
                key, source = args
                event = dict(key=state['object_prefix'] + key,
                             sha256=hashlib.sha256(Path(source).read_bytes()).hexdigest())
                with open(state['journal'], 'a') as stream:
                    stream.write(json.dumps(event) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
            result = super()._command(operation, *args)
            if crash and operation == 'put' and '/packs/' in args[0]:
                os._exit(71)  # Actual process loss after upload, before publication.
            return result
    return JournaledArchive(state['cache'], state['executable'])


def crash_worker(receipt):
    from gitpickle_jobs import Jobs
    state = json.loads(Path(receipt).read_text())
    with cloud(state, crash=True), Jobs(state['cache'], disk_reserve=0, source_interval=0) as jobs:
        job = jobs.accept('acceptance', 'backup', 'upload-crash', 'crash', 'local',
                          {'repo': state['source']})
        jobs.start()
        jobs.follow(job['id'])
    raise RuntimeError('Upload fault did not execute')


def run():
    import base64
    import gitpickle_archive as a
    import gitpickle_perf as fixtures
    import gitpickle_recovery as recovery
    import gitpickle_security as security
    import gitpickle_test_resources as resources
    from gitpickle_dev_env import snapshot
    from gitpickle_github import GitHub, Protection
    from gitpickle_jobs import Jobs, JobError
    from gitpickle_writer import Writer

    before = snapshot()
    artifact = Path(os.environ['LOOPER_ARTIFACT_DIR']).resolve()
    prefix = os.environ['LOOPER_RESOURCE_PREFIX']
    run_id = 't022-' + uuid.uuid4().hex[:24]
    base = artifact / run_id
    base.mkdir(mode=0o700)
    executable = str(Path('target/debug/gitpickle').resolve())
    receipt = artifact / (run_id + '.json')
    state = dict(resource_prefix=prefix, uid=os.getuid(), run=run_id,
        object_prefix=BASE + prefix + '/recovery/' + run_id + '/', bucket=BUCKET,
        endpoint=ENDPOINT, journal=str(artifact / (run_id + '.jsonl')),
        cache=str(base / 'cache'), source=str(base / 'source.git'), executable=executable)
    validate(state, prefix)
    storage = resources.client()  # Validate authorized persistent bucket configuration.
    assert not storage.list_objects_v2(Bucket=BUCKET, Prefix=state['object_prefix']).get('Contents')
    save(receipt, state)
    Path(state['journal']).touch(mode=0o600, exist_ok=False)
    runner = artifact / (run_id + '-cleanup.py')
    shutil.copyfile(__file__, runner)
    with open(os.environ['LOOPER_RESOURCE_CLEANUP_FILE'], 'a') as stream:
        stream.write('\n# T022: exact journaled keys only; preserve persistent bucket\n')
        stream.write('(set +x; source /home/agent/.config/gitpickle/r2-test.env; python3 ' +
            shlex.join([str(runner), 'cleanup', str(receipt), prefix]) + ')\n')
        stream.flush()
        os.fsync(stream.fileno())

    report = dict(schema_version=1, task_id='T022', started=datetime.now(timezone.utc).isoformat(),
        resource=state, checks={}, limitations=[], status='incomplete')
    result_file = base / 'result.json'
    def record(name, value):
        report['checks'][name] = value
        save(result_file, report)
        label = 'recorded' if name == 'github_wiki_capability' else 'passed'
        print(name + ': ' + label, flush=True)
    def timed(function):
        start = time.monotonic()
        result = function()
        return result, round(time.monotonic() - start, 3)
    def payload_count():
        return sum('/packs/' in e['key'] or '/lfs/' in e['key']
                   for e in map(json.loads, Path(state['journal']).read_text().splitlines()))
    def completed(jobs, job):
        jobs.start()
        outcome = jobs.follow(job['id'])
        assert outcome['state'] == 'completed', outcome.get('error')
        return outcome['result']
    try:
        # Run bounded deterministic failure and scheduling acceptance before live work.
        suites = ['capture', 'recovery', 'components', 'jobs', 'github', 'cloud']
        for suite in suites:
            with (base / (suite + '.log')).open('w') as log:
                code = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', 'tools', '-p', 'test_gitpickle_' + suite + '.py', '-v'],
                    stdout=log, stderr=subprocess.STDOUT, timeout=240).returncode
            assert code == 0, suite + ' acceptance failed'
        record('local_acceptance', dict(suites=suites, logs=str(base), passed=True))
        gh_report = base / 'github-resource.json'
        assert resources.provision(gh_report) == 0, 'Disposable GitHub setup failed'
        provision = json.loads(gh_report.read_text())
        gh = provision['github']
        alias, identity = gh['repository'], gh['id']
        token = resources.command(['gh', 'auth', 'token']).strip()
        github = GitHub(lambda: token)
        # Limit discovery to the owned fixture. Never enumerate or protect unrelated repositories.
        github.repositories = lambda: {str(identity): github.get(identity)}
        repo_id = 'github-' + str(identity)
        source = Path(state['source'])
        fixtures.make_repo(source, 'T022-private', 3, 3072)
        original_refs = fixtures.refs(source)
        env = security.git_environment()
        env.update(GIT_ALLOW_PROTOCOL='https', GIT_CONFIG_COUNT='1',
            GIT_CONFIG_KEY_0='http.https://github.com/.extraHeader',
            GIT_CONFIG_VALUE_0='Authorization: Basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode())
        remote = 'https://github.com/' + alias + '.git'
        def push():
            security.run(['git', '-C', str(source), 'push', '--mirror', remote], env=env, timeout=120)
        push()
        os.environ['GITPICKLE_STORAGE_CONFIG'] = json.dumps(dict(provider='r2', endpoint=ENDPOINT,
            region='auto', bucket=BUCKET, prefix=state['object_prefix']))
        with cloud(state), Jobs(state['cache'], source_adapter=github, disk_reserve=0, source_interval=0) as jobs:
            protection = Protection(jobs, github)
            preview = protection.preview([identity])
            protection.protect(preview, preview['confirmation'])
            job = protection.backup([identity], key='private-first', staging_bytes=1024*1024)[0]
            first, elapsed = timed(lambda: completed(jobs, job))
            record('private_capture', dict(repository=alias, private=gh['private'], point=first,
                job_id=job['id'], seconds=elapsed, expected_refs=original_refs))
            count = payload_count()
            unchanged = completed(jobs, protection.backup([identity], key='private-no-change', staging_bytes=1024*1024)[0])
            assert unchanged == first and payload_count() == count
            record('private_no_change', dict(point=first, payload_puts=0))
            # Replace all refs with unrelated history and delete the captured tag/branch.
            replacement = base / 'replacement.git'
            fixtures.make_repo(replacement, 'T022-replacement', 1, 512)
            security.run(['git', '-C', str(source), 'fetch', str(replacement), 'main'],
                         env=dict(security.git_environment(), GIT_ALLOW_PROTOCOL='file'))
            new_head = a.git(replacement, 'rev-parse', 'HEAD').decode().strip()
            a.git(source, 'update-ref', 'refs/heads/main', new_head)
            a.git(source, 'update-ref', '-d', 'refs/heads/topic')
            a.git(source, 'update-ref', '-d', 'refs/tags/v1')
            push()
            second = completed(jobs, protection.backup([identity], key='force-push', staging_bytes=1024*1024)[0])
            assert second != first
            record('force_push', dict(old_point=first, new_point=second, new_head=new_head))

            # Upload a deterministic LFS object to the real private repository.
            payload = b'T022 deterministic required LFS content\n' * 64
            oid = a.digest(payload)
            lfs_path = source / 'lfs/objects' / oid[:2] / oid[2:4] / oid
            lfs_path.parent.mkdir(parents=True)
            lfs_path.write_bytes(payload)
            pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode()
            blob = a.git(source, 'hash-object', '-w', '--stdin', data=pointer).decode().strip()
            tree = a.git(source, 'mktree', data=f'100644 blob {blob}\tlarge.bin\n'.encode()).decode().strip()
            commit = fixtures.git(source, 'commit-tree', tree, '-p', new_head, data=b'LFS fixture\n').decode().strip()
            a.git(source, 'update-ref', 'refs/heads/main', commit)
            security.run(['git', '-C', str(source), '-c', 'lfs.url=' + remote + '/info/lfs',
                'lfs', 'push', '--all', remote], env=env, timeout=180)
            push()
            latest = completed(jobs, protection.backup([identity], key='private-lfs', staging_bytes=1024*1024)[0])
            assert recovery.point_info(Path(state['cache']), latest)['complete']
            assert a.read(Path(state['cache']), latest, 'point')['lfs']
            record('private_lfs', dict(point=latest, oid=oid, bytes=len(payload)))
            latest_refs = fixtures.refs(source)
        # Crash a separate process after a real R2 pack upload. Its accepted job survives.
        crashed = subprocess.run([sys.executable, '-B', __file__, 'crash', str(receipt)], timeout=240)
        assert crashed.returncode == 71
        shutil.rmtree(state['cache'])
        with cloud(state), Jobs(state['cache'], disk_reserve=0, source_interval=0) as jobs:
            assert a.inventory(Path(state['cache']), 'crash') == []
            job = jobs.accept('acceptance', 'backup', 'upload-crash', 'crash', 'local', {'repo': str(source)})
            crash_point = completed(jobs, job)
            assert len(a.inventory(Path(state['cache']), 'crash')) == 1
            completed_ids = {key: item['state'] for key, item in jobs.jobs.items()}
            record('upload_process_crash', dict(exit_code=71, unpublished_before_retry=True,
                job_id=job['id'], replay_point=crash_point, publications=1))

        # A small deterministic portfolio exercises actual Git, LFS, and linked wiki bytes on R2.
        with cloud(state), Writer(state['cache']) as writer:
            cache = Path(state['cache'])
            wiki = base / 'wiki.git'
            fixtures.make_repo(wiki, 'T022-wiki', 2, 1024)
            wiki_refs = fixtures.refs(wiki)
            child = writer.capture(wiki, 'components-wiki', 'fixture:wiki')
            component = writer.capture(source, 'components', 'fixture:components', wiki=False)
            assert not recovery.point_info(cache, component)['complete']
            complete = writer.capture(source, 'components', 'fixture:components', source / 'lfs/objects', wiki=child)
            assert recovery.point_info(cache, complete)['complete']
            count = payload_count()
            assert writer.capture(wiki, 'components-wiki', 'fixture:wiki') == child
            assert writer.capture(source, 'components', 'fixture:components', source / 'lfs/objects', wiki=child) == complete
            assert payload_count() == count
            portfolio = []
            for index, commits in enumerate((0, 1, 3)):
                path = base / ('small-' + str(index) + '.git')
                fixtures.make_repo(path, 'T022-small-' + str(index), commits, commits * 256)
                key = writer.capture(path, 'small-' + str(index), 'fixture:small')
                portfolio.append(dict(repository='small-' + str(index), point=key, refs=fixtures.refs(path)))
            record('r2_components', dict(incomplete_point=component, complete_point=complete,
                wiki_point=child, wiki_refs=wiki_refs, no_change_payload_puts=0,
                wiki_source='deterministic local Git fixture', portfolio=portfolio))
        # GitHub private wiki availability is a separate source capability.
        wiki_probe = subprocess.run(['gh', 'api', '--method', 'PATCH', 'repos/' + alias,
                                    '-F', 'has_wiki=true'], capture_output=True, text=True, timeout=60)
        wiki_enabled = wiki_probe.returncode == 0 and json.loads(wiki_probe.stdout).get('has_wiki') is True
        report['limitations'].append('Enabled wiki recovery uses a deterministic local Git fixture on R2; live private GitHub wiki capture is not established.')
        record('github_wiki_capability', dict(enable_request_exit=wiki_probe.returncode, has_wiki=wiki_enabled,
            live_capture_verified=False))

        # Remove only the fixture through its previously registered ownership checks.
        operation_config = os.environ['GITPICKLE_STORAGE_CONFIG']
        os.environ['GITPICKLE_STORAGE_CONFIG'] = json.dumps(dict(provider='r2', endpoint=ENDPOINT,
            region='auto', bucket=BUCKET, prefix=BASE))
        try:
            resources.cleanup(Path(provision['cleanup']['receipt']), prefix)
        finally:
            os.environ['GITPICKLE_STORAGE_CONFIG'] = operation_config
        assert resources.api('repos/' + alias, True) is None
        try:
            github.get(identity)
        except JobError as error:
            assert error.code == 'source_unavailable'
        else:
            raise AssertionError('Deleted source remains accessible')
        record('source_deletion', dict(repository=alias, api='404', retained_points=[first, latest]))
        shutil.rmtree(state['cache'])
        for path in base.glob('*.git'):
            shutil.rmtree(path)
        assert not Path(state['cache']).exists() and not source.exists()

        # Guard only this process tree. Do not change host DNS, firewall, or other workloads.
        guard = base / 'network-guard.so'
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror',
                        'tools/recovery_network_guard.c', '-ldl', '-o', str(guard)], check=True)
        clean_env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in
            ('GITHUB', 'GH_TOKEN', 'GH_CONFIG', 'PROXY'))}
        clean_env.update(LD_PRELOAD=str(guard), GITPICKLE_TEST_STORAGE_HOST=HOST,
                         HOME=str(base / 'no-credentials'), GIT_TERMINAL_PROMPT='0')
        for host in ('github.com', 'api.github.com'):
            test = subprocess.run([sys.executable, '-I', '-c',
                'import socket,sys; socket.getaddrinfo(sys.argv[1],443)', host],
                env=clean_env, capture_output=True)
            assert test.returncode != 0
        def restore_cli(*args):
            command = [executable, 'restore', '--from-storage', '--provider', 'r2',
                '--endpoint', ENDPOINT, '--region', 'auto', '--bucket', BUCKET,
                '--prefix', state['object_prefix'], '--json', *map(str, args)]
            outcome = subprocess.run(command, env=clean_env, capture_output=True, timeout=240)
            assert outcome.returncode == 0, 'Storage-only CLI restore failed'
            return json.loads(outcome.stdout)['data']
        inventory = restore_cli('--inventory')
        save(base / 'inventory.json', inventory)
        restored = []
        targets = [(repo_id, first, original_refs), (repo_id, latest, latest_refs),
                   ('components', complete, latest_refs)]
        targets += [(p['repository'], p['point'], p['refs']) for p in portfolio]
        for index, (repository, point, refs) in enumerate(targets):
            destination = base / ('recovered-' + str(index))
            result, elapsed = timed(lambda: restore_cli('--point', point, '--destination', destination))
            assert result['complete'] and result['git_integrity'] == 'passed'
            assert fixtures.refs(destination) == refs
            if repository == 'components':
                assert result['verified_components'] == ['git', 'lfs', 'wiki']
                assert fixtures.refs(destination / 'wiki.git') == wiki_refs
            save(base / ('restore-' + str(index) + '.json'), result)
            restored.append(dict(repository=repository, point=point, seconds=elapsed,
                                 verified_components=result['verified_components']))
        record('storage_only_restores', dict(github_dns_blocked=True, source_credentials=False,
            original_cache_removed=True, restores=restored))
        # Rebuild SQLite and capture indexes from a fresh download of the remote archive.
        with cloud(state), Jobs(state['cache'], disk_reserve=0, source_interval=0) as jobs:
            assert {key: item['state'] for key, item in jobs.jobs.items()} == completed_ids
            for repository, point, _ in targets:
                a.rebuild_projection(Path(state['cache']), repository)
                assert point in [a.read(Path(state['cache']), key, 'publication')['point']
                                 for key in a.inventory(Path(state['cache']), repository)]
            record('index_rebuild', dict(job_states=completed_ids, captured_repositories=len(set(t[0] for t in targets))))
        # Corrupt only a downloaded copy. Recovery must fail without exposing a destination.
        cache = Path(state['cache'])
        pack = cache / a.read(cache, latest, 'point')['packs'][0]['key']
        pack.write_bytes(b'corrupt downloaded copy')
        try:
            a.restore(cache, latest, base / 'must-not-exist')
        except ValueError:
            pass
        else:
            raise AssertionError('Corrupt pack accepted')
        assert not (base / 'must-not-exist').exists()
        record('corruption_rejected', dict(destination_absent=True, scope='downloaded copy only'))
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'blocked'
        # Keep provider messages and credentials out of evidence.
        report['failure'] = dict(type=type(error).__name__,
                                stage=list(report['checks'])[-1] if report['checks'] else 'local_acceptance')
        raise
    finally:
        report['source_unchanged'] = snapshot() == before
        report['finished'] = datetime.now(timezone.utc).isoformat()
        save(result_file, report)
        destination = Path('tests/results/recovery/t022.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        save(destination, report)
        print('Evidence: ' + str(result_file), flush=True)
    assert report['source_unchanged'], 'Verification changes project files'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'cleanup', 'crash'))
    parser.add_argument('receipt', nargs='?')
    parser.add_argument('prefix', nargs='?')
    args = parser.parse_args()
    if sys.flags.optimize:
        raise RuntimeError('Ownership checks require Python assertions')
    if args.action == 'cleanup':
        cleanup(args.receipt, args.prefix)
    elif args.action == 'crash':
        crash_worker(args.receipt)
    else:
        run()
