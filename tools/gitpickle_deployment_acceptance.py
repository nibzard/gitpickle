"""Run bounded binary or Docker/R2 acceptance with journaled, owned cleanup.

Source the authorized R2 test runtime file first. This does not provision a VM.
"""
import argparse
from contextlib import ExitStack
import platform
import signal
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid

from gitpickle_deployment_probe import PROCESS_CLEANUP, create_fixture, https_fixture, git_probe

import gitpickle_archive as archive
from gitpickle_jobs import Jobs
import gitpickle_portfolio_recovery as recovery
import gitpickle_test_resources as resources


def docker(*args, **kwargs):
    return subprocess.check_output(['docker', *args], text=True, **kwargs).strip()


def main():
    from gitpickle_dev_env import snapshot
    before = snapshot()
    parser = argparse.ArgumentParser(description=__doc__)
    profile = parser.add_mutually_exclusive_group(required=True)
    profile.add_argument('--image', help='Locally built immutable image ID or digest')
    profile.add_argument('--binary', type=Path, help='Verified release executable')
    parser.add_argument('--upgrade-image', help='A compatible, different release image to test an upgrade')
    parser.add_argument('--host-network', action='store_true',
                        help='Use host networking with a free loopback port when bridge networking is unavailable')
    args = parser.parse_args()
    if args.binary and (args.upgrade_image or args.host_network):
        parser.error('Container options require --image')
    artifact = Path(os.environ['LOOPER_ARTIFACT_DIR']).resolve()
    prefix = os.environ['LOOPER_RESOURCE_PREFIX']
    run = 't022-' + uuid.uuid4().hex[:24]  # Reuse the strict portfolio cleanup format.
    base = artifact / ('deployment-' + run)
    base.mkdir(mode=0o700)
    state = dict(resource_prefix=prefix, uid=os.getuid(), run=run,
        object_prefix=recovery.BASE + prefix + '/recovery/' + run + '/',
        bucket=recovery.BUCKET, endpoint=recovery.ENDPOINT,
        journal=str(artifact / (run + '.jsonl')), cache=str(base / 'cache'),
        executable=str((args.binary or Path('dist/gitpickle')).resolve()))
    storage = resources.client()
    assert not storage.list_objects_v2(Bucket=state['bucket'], Prefix=state['object_prefix']).get('Contents')
    recovery.validate(state, prefix)
    receipt = artifact / (run + '.json')
    recovery.save(receipt, state)
    Path(state['journal']).touch(mode=0o600, exist_ok=False)
    runner = artifact / (run + '-cleanup.py')
    shutil.copyfile(recovery.__file__, runner)
    labels = ['--label', 'io.looper.resource-prefix=' + prefix,
              '--label', 'io.looper.artifact=' + str(artifact)]
    # Recovery remains independent of this worktree. Register before mutation.
    with open(os.environ['LOOPER_RESOURCE_CLEANUP_FILE'], 'a') as stream:
        stream.write('\n# T029 remote fixture: reuse content-checked portfolio cleanup\n')
        stream.write('(set +x; source /home/agent/.config/gitpickle/r2-test.env; python3 ' +
            shlex.join([str(runner), 'cleanup', str(receipt), prefix]) + ')\n')
        for kind, command in ([('containers', ['ps', '-aq']), ('volumes', ['volume', 'ls', '-q'])] if args.image else []):
            filters = ['--filter', 'label=io.looper.resource-prefix=' + prefix,
                       '--filter', 'label=io.looper.artifact=' + str(artifact)]
            remove = ['docker', 'rm', '-f'] if kind == 'containers' else ['docker', 'volume', 'rm']
            stream.write('for owned in $(' + shlex.join(['docker', *command, *filters]) + '); do ' +
                         shlex.join(remove) + ' "$owned" >/dev/null 2>&1 || true; done\n')
        stream.flush()
        os.fsync(stream.fileno())
    image = docker('image', 'inspect', args.image, '--format', '{{.Id}}') if args.image else None
    upgrade = docker('image', 'inspect', args.upgrade_image, '--format', '{{.Id}}') if args.upgrade_image else image
    config = json.loads(os.environ['GITPICKLE_STORAGE_CONFIG'])
    config['prefix'] = state['object_prefix']
    os.environ['GITPICKLE_STORAGE_CONFIG'] = json.dumps(config)
    os.environ['GITPICKLE_SERVER_ID'] = prefix + '-deployment'
    os.environ['GITPICKLE_MANAGEMENT_TOKENS'] = json.dumps({'test-' + 'x' * 40: dict(actor='owner', agent=False)})
    binary_digest = hashlib.sha256(Path(state['executable']).read_bytes()).hexdigest()
    source = base / 'source'
    expected_head = create_fixture(source)
    recovery.save(base / 'fixture.json', dict(repository='r', head=expected_head, bytes=26))
    with recovery.cloud(state), Jobs(state['cache'], disk_reserve=0, source_interval=0) as jobs:
        job = jobs.accept('test', 'backup', 'deployment', 'r', 'local', {'repo': str(source)})
        jobs.start()
        result = jobs.follow(job['id'])
        assert result['state'] == 'completed'
    expected = {str(path.relative_to(state['cache'])): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in Path(state['cache']).rglob('*') if path.is_file() and
        '.projection' not in path.parts and path.name != '.writer.lock'}
    policy = base / 'policy.json'
    policy.write_text(json.dumps(dict(revision=1, grants=[dict(actor='owner', permission='git_read',
        repositories=['r'], expires_at=int(time.time()) + 3600)], approvers=[],
        sandbox='/var/lib/gitpickle/restores' if image else str(base))))
    policy.chmod(0o600)
    if image:
        subprocess.run(['sudo', '-n', 'chown', '10001:10001', str(policy)], check=True)
    containers, volumes = [], []
    host_ports = {}
    launch_times = {}
    checks = {}
    if not image:
        cleanup_program = base / 'process-cleanup.py'
        cleanup_program.write_text(PROCESS_CLEANUP)
        with open(os.environ['LOOPER_RESOURCE_CLEANUP_FILE'], 'a') as stream:
            stream.write('python3 ' + shlex.join([str(cleanup_program), state['executable'],
                str(base / 'binary-state')]) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    stack = ExitStack()
    proxy = stack.enter_context(https_fixture(base))
    processes = []
    binary_logs = []
    # Recovery must work after the only source checkout has disappeared.
    shutil.rmtree(source)
    shutil.rmtree(state['cache'])
    def boot(volume, name, selected_image=image):
        launched = time.monotonic()
        command = ['run', '-d', '--name', name, *labels, '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges:true', '--pids-limit=128', '--memory=512m',
            '--tmpfs', '/tmp:mode=1777,size=256m',
            '-v', str(volume) + ':/var/lib/gitpickle', '-v', str(policy) + ':/run/gitpickle/policy.json:ro']
        port = None
        if not image:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            log = open(base / ('daemon-' + str(len(processes)) + '.log'), 'w')
            binary_logs.append(log)
            process = subprocess.Popen([state['executable'], 'serve', '--state', str(volume),
                '--policy', str(policy), '--port', str(port), '--enable-git', '--git-base-url', proxy.url],
                stdout=log, stderr=log, env=dict(os.environ), start_new_session=True)
            processes.append(process)
            launch_times[process] = launched
            host_ports[process] = port
            return process
        if args.host_network:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            command += ['--network', 'host', '--health-cmd',
                "python3 -I -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:" + str(port) + "/readyz',timeout=2).read()\""]
        else:
            command += ['-p', '127.0.0.1::8080']
        for key in ('GITPICKLE_SERVER_ID', 'GITPICKLE_MANAGEMENT_TOKENS', 'GITPICKLE_STORAGE_CONFIG',
                    'GITPICKLE_STORAGE_ACCESS_KEY', 'GITPICKLE_STORAGE_SECRET_KEY'):
            command += ['--env', key]
        if os.environ.get('GITPICKLE_STORAGE_SESSION_TOKEN'):
            command += ['--env', 'GITPICKLE_STORAGE_SESSION_TOKEN']
        command += [selected_image]
        if args.host_network:
            command += ['--state', '/var/lib/gitpickle/archive', '--policy', '/run/gitpickle/policy.json',
                        '--host', '127.0.0.1', '--port', str(port), '--staging-bytes', '1073741824',
                        '--enable-git', '--git-base-url', proxy.url]
        else:
            command += ['--state', '/var/lib/gitpickle/archive', '--policy', '/run/gitpickle/policy.json',
                        '--host', '0.0.0.0', '--container-network', '--port', '8080',
                        '--staging-bytes', '1073741824', '--enable-git', '--git-base-url', proxy.url]
        identifier = docker(*command)
        containers.append(identifier)
        launch_times[identifier] = launched
        host_ports[identifier] = port
        return identifier
    def ready(identifier):
        started = launch_times[identifier]
        first_ready = None
        while time.monotonic() - started < 90:
            if not image:
                assert identifier.poll() is None, 'Binary exited; inspect daemon logs in ' + str(base)
                info = dict(State=dict(Running=True))
            else:
                info = json.loads(docker('inspect', identifier))[0]
            if not info['State']['Running']:
                raise RuntimeError('Container exited: ' + docker('logs', identifier, stderr=subprocess.STDOUT))
            port = host_ports[identifier] or info['NetworkSettings']['Ports']['8080/tcp'][0]['HostPort']
            try:
                with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/readyz', timeout=2) as response:
                    assert json.load(response) == dict(status='ready')
                proxy.backend = int(port)
                if first_ready is None:
                    first_ready = round(time.monotonic() - started, 3)
                    checks.setdefault('readiness_seconds', []).append(first_ready)
                if not image or info['State'].get('Health', {}).get('Status') == 'healthy':
                    return round(time.monotonic() - started, 3)
            except OSError:
                pass
            time.sleep(0.25)
        raise RuntimeError('Container readiness timeout: ' + docker('logs', identifier))
    def verify(identifier):
        checks.setdefault('git_access', []).append(git_probe(proxy, base, 'test-' + 'x' * 40, str(len(checks.get('git_access', []))), expected_head))
        if not image:
            actual = {str(p.relative_to(binary_state)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in binary_state.rglob('*') if p.is_file() and '.projection' not in p.parts
                and p.name != '.writer.lock'}
            assert actual == expected
            return
        assert docker('exec', identifier, 'id', '-u') == '10001'
        # Compare every committed and accepted record, not just one recovery point.
        script = "import pathlib,hashlib,json; p=pathlib.Path('/var/lib/gitpickle/archive'); print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file() and '.projection' not in f.parts and f.name != '.writer.lock'}))"
        assert json.loads(docker('exec', identifier, 'python3', '-I', '-c', script)) == expected
        docker('exec', identifier, 'rm', '-rf', '/tmp/restored')
        docker('exec', identifier, 'gitpickle', 'restore', '--from-storage', '/var/lib/gitpickle/archive',
               '--repository', 'r', '--destination', '/tmp/restored', '--yes', '--json')
        assert docker('exec', identifier, 'git', '-C', '/tmp/restored', 'show', 'refs/heads/main:data') == 'durable deployment fixture'
    def stop(identifier):
        if not image:
            os.killpg(identifier.pid, signal.SIGTERM)
            assert identifier.wait(timeout=30) == 0
            return
        docker('stop', '--time', '30', identifier)
        assert json.loads(docker('inspect', identifier))[0]['State']['ExitCode'] == 0
        assert 'stopped' in docker('logs', identifier, stderr=subprocess.STDOUT)
    try:
        volume = docker('volume', 'create', *labels, prefix + '-deployment-' + uuid.uuid4().hex[:8]) if image else base / 'binary-state'
        binary_state = volume
        if image:
            volumes.append(volume)
        current = boot(volume, prefix + '-deployment-' + uuid.uuid4().hex[:8])
        checks['install_to_healthy_seconds'] = ready(current)
        checks['installed_version'] = docker('exec', current, 'gitpickle', '--version') if image else subprocess.check_output([state['executable'], '--version'], text=True).strip()
        verify(current)
        stop(current)
        if image:
            launch_times[current] = time.monotonic()
            docker('start', current)
        else:
            current = boot(volume, '')
        checks['restart_to_healthy_seconds'] = ready(current)
        verify(current)
        stop(current)
        replacement = boot(volume, prefix + '-deployment-' + uuid.uuid4().hex[:8], upgrade)
        checks['replacement_to_healthy_seconds'] = ready(replacement)
        checks['replacement_version'] = docker('exec', replacement, 'gitpickle', '--version') if image else checks['installed_version']
        if args.upgrade_image:
            assert checks['installed_version'] != checks['replacement_version'], 'Upgrade must use a different version'
        verify(replacement)
        stop(replacement)
        if image:
            fresh = docker('volume', 'create', *labels, prefix + '-deployment-' + uuid.uuid4().hex[:8])
            volumes.append(fresh)
        else:
            shutil.rmtree(binary_state)
            fresh = binary_state
        recovered = boot(fresh, prefix + '-deployment-' + uuid.uuid4().hex[:8], upgrade)
        checks['empty_volume_to_healthy_seconds'] = ready(recovered)
        verify(recovered)
        stop(recovered)
        assert hashlib.sha256(Path(state['executable']).read_bytes()).hexdigest() == binary_digest
        report = dict(task_id='T029', status='passed', image_id=image, upgrade_image_id=upgrade, checks=checks,
            profile='container' if image else 'binary',
            binary_sha256=binary_digest,
            platform=platform.platform(), git_version=subprocess.check_output(['git', '--version'], text=True).strip(),
            network='host-loopback' if args.host_network or not image else 'bridge-loopback-publish',
            archive_records=len(expected), accepted_job=job['id'], expected_head=expected_head,
            source_removed=not source.exists(), seed_cache_removed=not Path(state['cache']).exists(),
            limitations=['Replacement uses the same release unless --upgrade-image supplies a different compatible version.',
                         'Fresh VM, Coolify, private HTTPS, and tailnet clients remain in T055.',
                         'TLS uses a trusted local test certificate and a bounded loopback fixture, not Tailscale Serve.',
                         'Cold means new application state; OS caches and provider caches are not cleared.',
                         'One tiny repository; three warm fetch samples per stage; no reliable p95 or scale claim.',
                         'Each Git request currently restores a temporary generation; warm means repeated client access.'])
        recovery.save(base / 'result.json', report)
        print(json.dumps(report, indent=2))
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        for log in binary_logs:
            log.close()
        stack.close()
        for identifier in containers:
            docker('rm', '-f', identifier)
        for volume in volumes:
            docker('volume', 'rm', volume)
        if image:
            subprocess.run(['sudo', '-n', 'chown', str(os.getuid()) + ':' + str(os.getgid()), str(policy)], check=True)
        assert snapshot() == before, 'Deployment checks changed project files'
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
