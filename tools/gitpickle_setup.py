"""Review and resume isolated Linux installation or a private Coolify recipe."""
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

# Run unchanged on either side of SSH. Never read or return credentials.
PROBE = r'''
import json, os, platform, shutil, subprocess
from pathlib import Path
home = str(Path.home())
tailscale = {'installed': bool(shutil.which('tailscale')), 'state': 'absent', 'dns': None}
if tailscale['installed']:
    tailscale['state'] = 'unavailable'
    try:
        p = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, timeout=10)
        v = json.loads(p.stdout)
        tailscale.update(state=v.get('BackendState', 'unavailable'), dns=v.get('Self', {}).get('DNSName'))
    except Exception:
        pass
manager = subprocess.run(['systemctl', '--user', 'is-system-running'], capture_output=True, timeout=10) if shutil.which('systemctl') else None
print(json.dumps(dict(libc=list(platform.libc_ver()), user_manager=manager.stdout.decode().strip() if manager else 'missing', home=home, uid=os.getuid(), system=platform.system(), machine=platform.machine(),
    machine_id=Path('/etc/machine-id').read_text().strip(), python=list(__import__('sys').version_info[:2]),
    dependencies={n: bool(shutil.which(n)) for n in ['git', 'git-lfs', 'prlimit', 'systemctl']},
    ca_certificates=Path('/etc/ssl/certs/ca-certificates.crt').is_file(), tailscale=tailscale)))
'''

# No service starts during installation. Runtime files remain operator-owned.
INSTALL = r'''
import base64, hashlib, json, os, stat, subprocess, sys
from pathlib import Path
p = json.load(sys.stdin)
os.umask(0o077)
if os.getuid() == 0 or os.getuid() != p['identity']['uid'] or str(Path.home()) != p['identity']['home']:
    raise SystemExit('Installation identity changed')
if Path('/etc/machine-id').read_text().strip() != p['identity']['machine_id']:
    raise SystemExit('Server identity changed')
home = Path.home()
def directory(path):
    # Reject symlinks in all ancestors before writing.
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink():
            raise SystemExit('Symlink in installation path')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise SystemExit('Installation directory has another owner')
def put(path, data, mode):
    if path.is_symlink():
        raise SystemExit('Symlink in installation path')
    if path.exists():
        if path.read_bytes() != data or stat.S_IMODE(path.stat().st_mode) != mode:
            raise SystemExit('Existing installation differs; no overwrite')
        return
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
binary = base64.b64decode(p['binary'], validate=True)
if hashlib.sha256(binary).hexdigest() != p['sha256']:
    raise SystemExit('Binary checksum mismatch')
release = home / '.local/share/gitpickle/releases' / p['sha256']
unit = home / '.config/systemd/user/gitpickle.service'
# Check conflicts before adding the release. Never replace another unit.
if unit.is_symlink() or (unit.exists() and unit.read_text() != p['unit']):
    raise SystemExit('Existing service differs; review an explicit upgrade')
directory(release)
directory(unit.parent)
directory(home / '.config/gitpickle')
if stat.S_IMODE((home / '.config/gitpickle').stat().st_mode) != 0o700:
    raise SystemExit('Runtime configuration directory must have mode 0700')
put(release / 'gitpickle', binary, 0o700)
put(unit, p['unit'].encode(), 0o600)
subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
print(json.dumps({'installed': True, 'service_started': False}))
'''

# Reuse identity, directory, and no-overwrite checks from installation.
CONFIGURE = INSTALL[:INSTALL.index("binary = base64")] + r'''
config = home / '.config/gitpickle'
directory(config)
if stat.S_IMODE(config.stat().st_mode) != 0o700:
    raise SystemExit('Configuration directory must be private')
# Check both files before writing either one. Interrupted writes are replayable.
for name, data in p['files'].items():
    path = config / name
    if path.is_symlink() or (path.exists() and (path.read_text() != data or stat.S_IMODE(path.stat().st_mode) != 0o600)):
        raise SystemExit('Existing runtime configuration differs; no overwrite')
if all((config / name).exists() for name in p['files']):
    print(json.dumps({'configured': True, 'service_started': False}))
    raise SystemExit(0)
sandbox = Path(json.loads(p['files']['policy.json'])['sandbox'])
directory(sandbox)
if stat.S_IMODE(sandbox.stat().st_mode) != 0o700:
    raise SystemExit('Restore sandbox must be private')
import shutil
runtime = {}
for line in p['files']['runtime.env'].splitlines():
    key, value = line.split('=', 1)
    runtime[key] = value[1:-1]
check = subprocess.run([p['executable'], '__archive-store', 'check'],
    env=dict(os.environ, **runtime), capture_output=True, timeout=120)
if check.returncode:
    raise SystemExit('Storage authentication or capabilities failed')
if shutil.disk_usage(sandbox).free < 2147483648:
    raise SystemExit('Insufficient staging disk')
for name, data in p['files'].items():
    put(config / name, data.encode(), 0o600)
print(json.dumps({'configured': True, 'service_started': False}))
'''


def runtime_plan(args, plan, ask):
    """Stage private runtime inputs once; the review receipt contains no secrets."""
    from gitpickle_github import GitHub
    from urllib.parse import urlsplit
    output = args.output.absolute()
    staged = output / 'runtime-inputs.json'
    if args.resume:
        if staged.is_symlink() or staged.stat().st_mode & 0o077:
            raise ValueError('Runtime inputs must be private')
        result = json.loads(staged.read_text())
        if any(getattr(args, key) and getattr(args, key) != value
               for key, value in result['review']['storage'].items()):
            raise ValueError('Storage scope changed. Review a new setup plan')
        return result
    location = {}
    for key, label in [('provider', 'Storage provider (r2 / s3 / b2)'),
                       ('endpoint', 'Storage HTTPS endpoint'), ('region', 'Storage region'),
                       ('bucket', 'Existing bucket'), ('prefix', 'Archive prefix ending in /')]:
        location[key] = getattr(args, key) or ask(label)
    endpoint = urlsplit(location['endpoint'])
    if (location['provider'] not in ('r2', 's3', 'b2') or endpoint.scheme != 'https'
            or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path not in ('', '/') or not re.fullmatch(r'[A-Za-z0-9._-]+', location['bucket'])
            or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_/-]*/', location['prefix'])
            or not re.fullmatch(r'[A-Za-z0-9_-]+', location['region'])):
        raise ValueError('Invalid storage location; use an existing bucket and a dedicated prefix')
    def secret(name, label):
        value = os.environ.get(name)
        if not value:
            if args.json or not sys.stdin.isatty():
                raise ValueError('Missing runtime credential: ' + name)
            value = getpass.getpass(label + ': ', stream=sys.stderr)
        if not value or any(ord(c) < 33 or ord(c) > 126 or c in "'$\\" for c in value):
            raise ValueError('Credential contains unsupported runtime-file characters')
        return value
    github = secret('GITPICKLE_GITHUB_TOKEN', 'GitHub token')
    access = secret('GITPICKLE_STORAGE_ACCESS_KEY', 'Storage access key')
    storage_secret = secret('GITPICKLE_STORAGE_SECRET_KEY', 'Storage secret key')
    management = secret('GITPICKLE_TOKEN', 'Management token (at least 32 random characters)')
    if len(management) < 32:
        raise ValueError('Management token must contain at least 32 characters')
    inventory = GitHub(lambda: github).repositories()
    if not inventory:
        raise ValueError('No authorized repositories found')
    ids = sorted('github-' + i for i in inventory)
    home = plan.get('identity', {}).get('home')
    sandbox = home + '/.local/state/gitpickle/restores' if home else '/var/lib/gitpickle/restores'
    expires = int(time.time()) + 365 * 86400
    policy = dict(revision=1, approvers=['owner'], sandbox=sandbox,
        grants=[dict(actor='owner', permission=permission, repositories=ids, expires_at=expires)
                for permission in ('protect', 'backup', 'sync', 'status', 'plan_restore', 'apply_plan')])
    values = dict(GITPICKLE_GITHUB_TOKEN=github, GITPICKLE_STORAGE_ACCESS_KEY=access,
        GITPICKLE_STORAGE_SECRET_KEY=storage_secret, GITPICKLE_SERVER_ID=uuid.uuid4().hex,
        GITPICKLE_STORAGE_CONFIG=json.dumps(location),
        GITPICKLE_MANAGEMENT_TOKENS=json.dumps({management: dict(actor='owner', agent=False)}))
    runtime = ''.join(key + "='" + value + "'\n" for key, value in values.items())
    result = dict(files={'policy.json': json.dumps(policy, indent=2) + '\n', 'runtime.env': runtime},
        review=dict(storage=location, authorized_repositories=[inventory[i]['alias'] for i in sorted(inventory)],
                    repository_ids=ids, grants_expire_at_unix_seconds=expires, time_zone='UTC',
                    bucket_creation=False, hosting=False, sandbox=sandbox,
                    changes=['Create private runtime.env and policy.json', 'Grant owner access to listed repositories for one year'],
                    protection='Configured only. Preview and authorize protection after starting the service.'))
    save(staged, (json.dumps(result, sort_keys=True) + '\n').encode())
    return result


COMPOSE = '''# Private deployment: keep Domains empty in Coolify.
services:
  gitpickle:
    image: IMAGE
    user: "10001:10001"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    restart: unless-stopped
    stop_grace_period: 65m
    ports: ["127.0.0.1:PORT:8080"]
    env_file: ["${GITPICKLE_RUNTIME_ENV:?Set an absolute private runtime file}"]
    volumes:
      - cache:/var/lib/gitpickle
      - ${GITPICKLE_POLICY:?Set an absolute private policy file}:/run/gitpickle/policy.json:ro
    tmpfs: ["/tmp:mode=1777,size=256m"]
volumes:
  cache:
'''


def digest(value):
    return hashlib.sha256(value).hexdigest()


def ssh(args):
    if not args.host or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*', args.host):
        raise ValueError('Use an explicit user@hostname for SSH')
    if not args.known_hosts or not args.known_hosts.is_file():
        raise ValueError('Supply a verified known_hosts file')
    # Ignore ambient SSH configuration. No agent forwarding, proxy commands, TOFU, or multiplex reuse.
    return ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(args.known_hosts.resolve()),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UpdateHostKeys=no',
            '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ConnectTimeout=10',
            '-p', str(args.ssh_port), args.host]


def execute(args, source, payload=None):
    command = ['python3', '-I', '-c', source]
    if args.target == 'ssh':
        command = ssh(args) + [shlex.join(command)]
    result = subprocess.run(command, input=json.dumps(payload).encode() if payload else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode:
        raise ValueError('Target command failed. Check pinned SSH identity, dependencies, and service conflicts')
    return json.loads(result.stdout)


def make_plan(args):
    if not 1 <= args.port <= 65535 or not 1 <= args.ssh_port <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    if args.target != 'ssh' and (args.host or args.known_hosts):
        raise ValueError('SSH settings require the SSH target')
    plan = dict(schema_version=1, target=args.target, port=args.port,
                privilege='Unprivileged user; no sudo or package installation',
                dependencies=['Linux x86-64, glibc 2.36+', 'Python 3.11+', 'Git', 'Git LFS',
                              'util-linux prlimit', 'CA certificates', 'systemd user manager'],
                configuration='Add a user service and immutable binary. Preserve all runtime files. Do not start a daemon.',
                hosting='Off. Backups require no inbound endpoint.',
                next='Run setup with --configure to generate private runtime files. See docs/setup.md.')
    if args.target == 'coolify':
        if args.binary:
            raise ValueError('Binary requires a local or SSH target')
        if not args.image or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}', args.image):
            raise ValueError('Coolify requires a verified image@sha256 digest')
        plan.update(image=args.image, configuration='Generate Compose only. No Coolify API calls or deployments.',
                    compose=COMPOSE.replace('PORT', str(args.port)).replace('IMAGE', args.image),
                    tailscale={'state': 'Inspect on the Coolify host; containers do not enroll in a tailnet'})
    else:
        if args.image:
            raise ValueError('Image requires Coolify')
        binary = args.binary or Path(os.environ.get('GITPICKLE_EXECUTABLE', ''))
        if not binary.is_file():
            raise ValueError('Supply the verified Linux binary with --binary')
        identity = execute(args, PROBE)
        if (identity['system'] != 'Linux' or identity['machine'] not in ('x86_64', 'amd64')
                or identity['uid'] == 0 or not re.fullmatch(r'/[A-Za-z0-9_./-]+', identity['home'])):
            raise ValueError('Use an unprivileged Linux x86-64 account with a simple absolute home path')
        checksum = digest(binary.read_bytes())
        home = identity['home']
        unit = f'''[Unit]
Description=GitPickle user service
After=network-online.target
[Service]
ExecStart={home}/.local/share/gitpickle/releases/{checksum}/gitpickle serve --state %S/gitpickle/archive --policy {home}/.config/gitpickle/policy.json --port {args.port} --staging-bytes 1073741824
EnvironmentFile={home}/.config/gitpickle/runtime.env
StateDirectory=gitpickle
StateDirectoryMode=0700
UMask=0077
Restart=on-failure
TimeoutStopSec=3900
KillMode=mixed
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%S/gitpickle
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=default.target
'''
        plan.update(identity=identity, sha256=checksum, binary=str(binary.resolve()), unit=unit,
                    tailscale=identity['tailscale'])
        if args.target == 'ssh':
            plan.update(host=args.host, ssh_port=args.ssh_port,
                        known_hosts_sha256=digest(args.known_hosts.read_bytes()))
        missing = [n for n, present in identity['dependencies'].items() if not present]
        if identity['python'] < [3, 11]:
            missing.append('Python 3.11+')
        if not identity['ca_certificates']:
            missing.append('CA certificates')
        libc_name, libc_version = identity['libc']
        if libc_name != 'glibc' or tuple(int(n) for n in libc_version.split('.')[:2]) < (2, 36):
            missing.append('glibc 2.36+')
        if identity['user_manager'] not in ('running', 'degraded'):
            missing.append('Active systemd user manager')
        plan['missing_dependencies'] = missing
    plan['private_https'] = dict(
        prerequisites='Existing authorized tailnet, MagicDNS, HTTPS certificates, allowed clients; inspect Serve status first',
        review_command='tailscale serve status --json',
        command=f'tailscale serve --bg --https=443 --set-path=/git http://127.0.0.1:{args.port}/git',
        effects='Manual opt-in after enabling Git hosting; no enrollment, Funnel, DNS, or Tailscale changes during setup',
        management='Keep /v1/management behind an SSH loopback tunnel; expose only /git')
    return plan


def save(path, content):
    if path.is_symlink():
        raise ValueError('Setup output must not be a symlink')
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('Existing setup output differs; use a new directory')
        return
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(content)
    path.chmod(0o600)


def main(argv=None):
    from gitpickle_workflow import Parser, approve, display, envelope
    from gitpickle_plans import PlanError
    from gitpickle_jobs import JobError
    argv = sys.argv[1:] if argv is None else argv
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--configure', action='store_true', help='Guide storage, GitHub credentials, and runtime configuration')
        for field in ('provider', 'endpoint', 'region', 'bucket', 'prefix'):
            parser.add_argument('--' + field)
        parser.add_argument('--target', choices=['local', 'ssh', 'coolify'])
        parser.add_argument('--host')
        parser.add_argument('--known-hosts', type=Path)
        parser.add_argument('--ssh-port', type=int, default=22)
        parser.add_argument('--binary', type=Path)
        parser.add_argument('--image')
        parser.add_argument('--port', type=int, default=8080)
        parser.add_argument('--output', type=Path)
        parser.add_argument('--resume', action='store_true')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--yes', action='store_true')
        parser.add_argument('--json', action='store_true')
        args = parser.parse_args(argv)
        def ask(label):
            if args.json or not sys.stdin.isatty():
                raise ValueError('Missing setup arguments. Run gitpickle setup --help')
            print(label + ': ', end='', file=sys.stderr, flush=True)
            return input().strip()
        if not args.target:
            args.target = ask('Run protection on (local / ssh / coolify)')
            if args.target not in ('local', 'ssh', 'coolify'):
                raise ValueError('Choose local, ssh, or coolify')
        if not args.output:
            args.output = Path(ask('Private directory for the installation plan'))
        if args.target == 'ssh':
            args.host = args.host or ask('Linux server (user@hostname)')
            args.known_hosts = args.known_hosts or Path(ask('Verified known_hosts file'))
        if args.target == 'coolify':
            args.image = args.image or ask('Verified image reference with @sha256 digest')
        if args.apply and not args.resume:
            raise ValueError('Review setup first; installation requires --resume --apply')
        plan = make_plan(args)
        output = args.output.absolute()
        if any(p.is_symlink() for p in [output, *output.parents]):
            raise ValueError('Setup directory must not contain symlinks')
        output.mkdir(parents=True, mode=0o700, exist_ok=True)
        if output.stat().st_mode & 0o077:
            raise ValueError('Setup directory must have mode 0700')
        runtime = runtime_plan(args, plan, ask) if args.configure else None
        if runtime:
            plan['runtime'] = runtime['review']
            plan['runtime_digest'] = digest(json.dumps(runtime, sort_keys=True).encode())
            plan['next'] = 'Start the service, then run protect github to review and authorize protection.'
        encoded = (json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()
        output = args.output.absolute()
        if any(p.is_symlink() for p in [output, *output.parents]):
            raise ValueError('Setup directory must not contain symlinks')
        receipt = output / 'plan.json'
        if args.resume:
            if not receipt.is_file() or receipt.is_symlink() or receipt.read_bytes() != encoded:
                raise ValueError('Setup plan changed. Review a new output directory before installation')
        else:
            output.mkdir(parents=True, mode=0o700, exist_ok=True)
            save(receipt, encoded)
        if args.target == 'coolify':
            save(output / 'compose.yaml', plan['compose'].encode())
            if runtime:
                for name, content in runtime['files'].items():
                    save(output / name, content.encode())
        print('Installation plan (no secrets):\n' + display(plan), file=sys.stderr)
        data = dict(plan=plan, plan_sha256=digest(encoded), installed=False)
        if args.apply:
            if args.target == 'coolify':
                raise ValueError('Deploy the reviewed Compose recipe through Coolify. See docs/setup.md')
            if plan['missing_dependencies']:
                raise ValueError('Install missing dependencies through your administrator before resuming')
            completed = output / 'applied.json'
            replay = completed.is_file() and not completed.is_symlink() and completed.read_text() == digest(encoded)
            if not replay:
                approve('Install this binary, user service, and reviewed runtime files? No service starts.', args)
            if runtime and not replay:
                from gitpickle_github import GitHub
                token_line = next(line for line in runtime['files']['runtime.env'].splitlines() if line.startswith('GITPICKLE_GITHUB_TOKEN='))
                inventory = GitHub(lambda: token_line.split('=', 1)[1][1:-1]).repositories()
                if sorted('github-' + i for i in inventory) != runtime['review']['repository_ids']:
                    raise ValueError('GitHub scope changed. Review a new setup plan')
            # Recheck binary bytes after review, before sending anything to the target.
            binary = Path(plan['binary']).read_bytes()
            if digest(binary) != plan['sha256']:
                raise ValueError('Binary changed after review')
            data.update(execute(args, INSTALL, dict(identity=plan['identity'], sha256=plan['sha256'],
                        binary=base64.b64encode(binary).decode(), unit=plan['unit'])))
            if runtime:
                data.update(execute(args, CONFIGURE, dict(identity=plan['identity'], files=runtime['files'], executable=plan['identity']['home'] + '/.local/share/gitpickle/releases/' + plan['sha256'] + '/gitpickle')))
            save(completed, digest(encoded).encode())
        print(json.dumps(envelope(data), sort_keys=True) if args.json else display(data))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, PlanError, JobError) as error:
        code = error.code if isinstance(error, PlanError) else 'setup_failed'
        print('Setup stopped. ' + (str(error) if isinstance(error, ValueError) else 'Check target access and configuration.'), file=sys.stderr)
        if '--json' in argv:
            print(json.dumps(envelope(error=code), sort_keys=True))
        return 2 if isinstance(error, (ValueError, PlanError)) else 5


if __name__ == '__main__':
    raise SystemExit(main())
