"""Exercise the packaged user unit with private paths and a free loopback port."""
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


def main():
    from gitpickle_dev_env import snapshot
    before = snapshot()
    prefix = os.environ['LOOPER_RESOURCE_PREFIX']
    base = Path(os.environ['LOOPER_ARTIFACT_DIR']).resolve() / ('systemd-' + uuid.uuid4().hex[:12])
    base.mkdir(mode=0o700)
    unit_name = prefix + '-deployment-' + uuid.uuid4().hex[:8] + '.service'
    state_name = unit_name.removesuffix('.service')
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / state_name
    assert not state.exists()
    unit = base / unit_name
    installed = Path.home() / '.config/systemd/user' / unit_name
    assert not installed.exists()
    token = 'test-only-' + uuid.uuid4().hex
    env = base / 'runtime.env'
    env.write_text("GITPICKLE_MANAGEMENT_TOKENS='" + json.dumps({token: dict(actor='owner', agent=False)}) + "'\n")
    env.chmod(0o600)
    policy = base / 'policy.json'
    policy.write_text(json.dumps(dict(revision=1, grants=[], approvers=[], sandbox=str(base))))
    policy.chmod(0o600)
    for release in ('old', 'new'):
        directory = base / release
        directory.mkdir()
        binary = os.environ.get('GITPICKLE_UPGRADE_BINARY', 'dist/gitpickle') if release == 'new' else 'dist/gitpickle'
        shutil.copy2(binary, directory / 'gitpickle')
    current = base / 'current'
    current.symlink_to(base / 'old', target_is_directory=True)
    installed_version = subprocess.check_output([str(current / 'gitpickle'), '--version'], text=True).strip()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    source = Path('deploy/systemd/gitpickle.service').read_text()
    source = source.replace('/opt/gitpickle/current', str(current))
    source = source.replace('%h/.config/gitpickle/policy.json', str(policy))
    source = source.replace('%h/.config/gitpickle/runtime.env', str(env))
    source = source.replace('%S/gitpickle', '%S/' + state_name)
    source = source.replace('StateDirectory=gitpickle', 'StateDirectory=' + state_name)
    source = source.replace('--port 8080', '--port ' + str(port))
    unit.write_text(source)
    # Cleanup checks the exact linked unit and state marker before removal.
    with open(os.environ['LOOPER_RESOURCE_CLEANUP_FILE'], 'a') as stream:
        stream.write('\n# T026: private systemd unit and marked state only\n')
        stream.write('if [ "$(readlink ' + shlex.quote(str(installed)) + ')" = ' + shlex.quote(str(unit)) + ' ]; then\n')
        stream.write(' systemctl --user stop ' + shlex.quote(unit_name) + ' || true\n')
        stream.write(' rm -f -- ' + shlex.quote(str(installed)) + '\n systemctl --user daemon-reload\nfi\n')
        stream.write('if [ ! -L ' + shlex.quote(str(state)) + ' ] && [ "$(cat ' + shlex.quote(str(state / '.deployment-owner')) + ' 2>/dev/null)" = ' + shlex.quote(prefix) + ' ]; then rm -rf -- ' + shlex.quote(str(state)) + '; fi\n')
        stream.flush()
        os.fsync(stream.fileno())
    # Mark the private state before startup so interrupted readiness is cleanable.
    state.mkdir(mode=0o700, parents=True)
    (state / '.deployment-owner').write_text(prefix)
    def control(*args):
        subprocess.run(['systemctl', '--user', *args], check=True)
    def ready():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/readyz', timeout=1) as response:
                    assert json.load(response) == dict(status='ready')
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError('User unit did not become ready; inspect journal for ' + unit_name)
    try:
        subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], check=True)
        control('link', str(unit))
        control('daemon-reload')
        control('start', unit_name)
        ready()
        control('restart', unit_name)
        ready()
        control('stop', unit_name)
        current.unlink()
        current.symlink_to(base / 'new', target_is_directory=True)
        control('start', unit_name)
        ready()
        replacement_version = subprocess.check_output([str(current / 'gitpickle'), '--version'], text=True).strip()
        if os.environ.get('GITPICKLE_UPGRADE_BINARY'):
            assert installed_version != replacement_version, 'Upgrade must use a different version'
        control('stop', unit_name)
        result = dict(task_id='T026', status='passed', checks=['install', 'restart', 'release-path replacement', 'SIGTERM'],
                      installed_version=installed_version, replacement_version=replacement_version,
                      limitations=['Local user manager; fresh VM acceptance remains in T055.'])
        (base / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    finally:
        if installed.is_symlink() and installed.resolve() == unit:
            subprocess.run(['systemctl', '--user', 'stop', unit_name], check=False)
            installed.unlink()
            control('daemon-reload')
        marker = state / '.deployment-owner'
        if not state.is_symlink() and marker.is_file() and marker.read_text() == prefix:
            shutil.rmtree(state)
    assert snapshot() == before, 'Systemd checks changed project files'


if __name__ == '__main__':
    main()
