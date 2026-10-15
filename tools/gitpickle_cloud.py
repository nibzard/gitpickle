"""Compose the archive writer with the existing native storage adapters.

Remote readback precedes local visibility. A failed remote write cannot be
reconciled from local bytes. No archive record or job acknowledgment depends
on the survival of the local cache. Migration from a local archive is explicit.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import uuid

import gitpickle_archive as a
from gitpickle_jobs import JobError


class CloudArchive:
    def __init__(self, root, executable, *, command=None):
        self.root = Path(root).resolve()
        self.executable = executable
        self.command = command or self._command
        self.original_put = a.put
        self.lock = threading.RLock()
        self.location = json.loads(os.environ['GITPICKLE_STORAGE_CONFIG'])
        if set(self.location) != {'provider', 'endpoint', 'region', 'bucket', 'prefix'}:
            raise ValueError('invalid storage configuration')

    def _command(self, operation, *arguments):
        result = subprocess.run([self.executable, '__archive-store', operation, *map(str, arguments)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=240, check=False)
        if result.returncode:
            raise JobError('storage_unavailable')
        return result.stdout

    def __enter__(self):
        capabilities = json.loads(self.command('capabilities'))
        publication = capabilities.get('archive_publication')
        supported = capabilities['conditional_create'] or (
            self.location['provider'] == 'b2' and publication == 'content-addressed-v1')
        if not supported or not capabilities['completed_write_visibility']:
            raise JobError('unsupported_capability')
        self.capabilities = capabilities
        # Download before acquiring writer authority. Existing local immutable
        # bytes must match the remote archive. Extra local records require a
        # clean cache or explicit migration, never implicit publication.
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-cloud-') as folder:
            downloaded = Path(folder)
            self.command('download', downloaded)
            local_files = [p for prefix in ('repos', 'ownership', 'control', 'jobs', 'protection', 'execution')
                           for p in (self.root / prefix).rglob('*') if p.is_file()]
            if (self.root / 'archive.json').exists():
                local_files.append(self.root / 'archive.json')
            for path in local_files:
                remote = downloaded / path.relative_to(self.root)
                if path.is_symlink() or not remote.is_file() or path.read_bytes() != remote.read_bytes():
                    raise JobError('archive_cache_conflict')
            for path in downloaded.rglob('*'):
                if path.is_file():
                    self.original_put(self.root, str(path.relative_to(downloaded)), path.read_bytes())
        a.put = self.put
        try:
            if not (self.root / 'archive.json').exists():
                a.initialize(self.root, uuid.uuid4().hex)
            return self
        except BaseException:
            a.put = self.original_put
            raise

    def __exit__(self, *_):
        a.put = self.original_put

    def put(self, root, key, data):
        if Path(root).resolve() != self.root:
            return self.original_put(root, key, data)
        a.security.archive_path(self.root, key)
        with self.lock:
            # The helper rechecks existing bytes and reconciles ambiguous writes
            # against remote bytes. Its failure remains a failure even if a local
            # cache contains an object left by an earlier attempt.
            with tempfile.TemporaryDirectory(dir=self.root,
                    prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-upload-') as folder:
                source = Path(folder) / 'object'
                source.write_bytes(data)
                self.command('put', key, source)
            self.original_put(root, key, data)

    def probe(self):
        key = '.probes/' + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(dir=self.root,
                prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-probe-') as folder:
            source = Path(folder) / 'readback'
            source.write_bytes(os.urandom(64))
            self.command('put', key, source)
        return dict(storage=self.location, capabilities=self.capabilities,
                    probe_key=key, bucket_creation=False)
