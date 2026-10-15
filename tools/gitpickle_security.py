"""Shared process, path, and network boundaries for trusted adapters."""
import ipaddress
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import tempfile


def private_directory(path):
    path = Path(path).absolute()
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError('unsafe state path')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('state directory must be private and owned by the service user')
    return path


def archive_path(root, key):
    if (not isinstance(key, str) or not key or '\\' in key
            or any(ord(c) < 32 for c in key)
            or any(p in ('', '.', '..') for p in key.split('/'))):
        raise ValueError('unsafe key')
    path = Path(root)
    if path.is_symlink():
        raise ValueError('symlink in archive')
    for part in key.split('/'):
        path /= part
        if path.is_symlink():
            raise ValueError('symlink in archive')
    return path


def git_environment():
    # Do not inherit loaders, proxies, Git configuration, or credential helpers.
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'TMPDIR',
           'LOOPER_RESOURCE_PREFIX') if key in os.environ}
    env.update(HOME='/nonexistent', LC_ALL='C', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
               GIT_NO_REPLACE_OBJECTS='1', GIT_ALLOW_PROTOCOL='')
    return env


GIT_OPTIONS = ['-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
               '-c', 'credential.helper=', '-c', 'core.askPass=',
               '-c', 'http.followRedirects=false', '-c', 'http.proxy=',
               '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
               '-c', 'protocol.ext.allow=never', '-c', 'protocol.file.allow=never',
               '-c', 'uploadpack.packObjectsHook=']


def run(command, *, input=None, env=None, timeout=3600, capture_output=True,
        check=True, max_output=64 * 1024 * 1024, output=None):
    """Bound elapsed time and output; kill the complete child group on failure.

    Output uses temporary files so a child cannot fill the parent's memory.
    A per-process file limit also bounds these files and generated pack files.
    prlimit applies limits before exec, without unsafe fork callbacks in threads.
    An output sink receives file-backed output in fixed-size chunks. The file
    limit still applies; max_output continues to bound captured diagnostics.
    """
    wrapped = ['prlimit', '--cpu=3600', '--fsize=17179869184', '--nofile=256',
               '--as=8589934592', '--', *command]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, \
            tempfile.TemporaryFile() as source:
        if input is not None:
            if hasattr(input, 'read'):
                shutil.copyfileobj(input, source, 64 * 1024)
            else:
                source.write(input)
            source.seek(0)
        process = subprocess.Popen(wrapped, stdin=source, stdout=out, stderr=err,
                                   env=env, start_new_session=True)
        import time
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or (output is None and out.tell() > max_output) or err.tell() > max_output:
                    raise subprocess.CalledProcessError(124, ['git'])
                try:
                    process.wait(timeout=0.02)
                except subprocess.TimeoutExpired:
                    pass
            if (output is None and out.tell() > max_output) or err.tell() > max_output:
                raise subprocess.CalledProcessError(124, ['git'])
            if check and process.returncode:
                raise subprocess.CalledProcessError(process.returncode, ['git'])
            out.seek(0)
            err.seek(0)
            if output is not None:
                shutil.copyfileobj(out, output, 64 * 1024)
                result = None
            else:
                result = out.read()
            return subprocess.CompletedProcess(['git'], process.returncode, result, err.read())
        finally:
            # Descendants must not outlive even a successful parent.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def github_addresses(host):
    if host not in ('github.com', 'api.github.com'):
        raise ValueError('unsupported source host')
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
        raise ValueError('unsafe source address')
    return addresses


def local_source(value):
    from urllib.parse import urlsplit, unquote
    value = str(value)
    if value.startswith('file:'):
        parsed = urlsplit(value)
        if parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError('unsafe clone source')
        value = unquote(parsed.path)
    path = Path(value)
    if (not path.is_absolute() or not path.is_dir()
            or not ((path / 'HEAD').is_file() and (path / 'objects').is_dir()
                    or (path / '.git').is_dir())):
        raise ValueError('unsafe clone source')
    return str(path)


def source_label(value):
    """Remove URL credentials before any durable record is created."""
    from urllib.parse import urlsplit, urlunsplit
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError('invalid source label')
    if '://' in value:
        url = urlsplit(value)
        if url.scheme not in ('https', 'http', 'ssh') or not url.hostname:
            raise ValueError('invalid source label')
        host = '[' + url.hostname + ']' if ':' in url.hostname else url.hostname
        if url.port:
            host += ':' + str(url.port)
        return urlunsplit((url.scheme, host, url.path, '', ''))
    return value


from contextlib import contextmanager


@contextmanager
def destination_parent(destination):
    """Reject traversal and symlinks, then pin the destination parent."""
    path = Path(destination).absolute()
    if '..' in path.parts or path.name in ('', '.', '..'):
        raise ValueError('unsafe destination')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('destination exists')
        yield Path(f'/proc/{os.getpid()}/fd/{fd}') / path.name
    finally:
        os.close(fd)
