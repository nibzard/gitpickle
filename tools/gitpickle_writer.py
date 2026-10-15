"""Single-writer coordinator for the local archive reference implementation.

The OS lock only coordinates processes using the same filesystem. Operators
must fence the former server outside this program before a takeover.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import threading


def server_identity():
    """Use a persistent installation identity, never a PID or a timestamp."""
    override = os.environ.get('GITPICKLE_SERVER_ID')
    if override:
        return override
    identity = Path('/etc/machine-id').read_bytes().strip()
    if not identity:
        raise ValueError('missing persistent server identity; set GITPICKLE_SERVER_ID')
    return hashlib.sha256(identity).hexdigest()


class Writer:
    def __init__(self, root, server_id=None):
        self.root = Path(root).resolve()
        self.server_id = server_id or server_identity()
        if not isinstance(self.server_id, str) or not self.server_id.strip():
            raise ValueError('missing persistent server identity')
        self._lock = None
        self._mutex = threading.RLock()
        self._repositories = {}
        self._stopped = False
        self._active = 0
        self.owner_key = None

    def __enter__(self):
        import gitpickle_archive as a
        a.read(self.root, 'archive.json', 'archive')
        # Never unlink the lock: a replacement inode could admit another writer.
        fd = os.open(self.root / '.writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self._lock = os.fdopen(fd, 'a+b')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            owners = self.ownership()
            if not owners:
                # Legacy archives require explicit reconciliation, not adoption.
                if any((self.root / 'repos').glob('*/commits/*')):
                    return self
                self.owner_key = self._publish_owner(None, None, self.reconcile())
            else:
                self.owner_key = owners[-1]
            self.reconcile()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        with self._mutex:
            if self._active:
                raise RuntimeError('wait for active mutations before closing the writer')
            if self._lock is not None:
                self._lock.close()
                self._lock = None

    def ownership(self):
        import gitpickle_archive as a
        archive_id = a.read(self.root, 'archive.json', 'archive')['archive_id']
        records = {}
        children = {}
        for path in (self.root / 'ownership').glob('*'):
            key = 'ownership/' + path.name
            value = a.read(self.root, key, 'writer')
            if value['archive_id'] != archive_id or not value['server_id']:
                raise ValueError('writer identity mismatch')
            records[key] = value
            parent = value['parent']
            if parent in children:
                raise ValueError('conflicting ownership chains')
            children[parent] = key
        for value in records.values():
            if value['parent'] is not None and value['parent'] not in records:
                raise ValueError('missing ownership predecessor')
        chain, parent = [], None
        while parent in children:
            parent = children[parent]
            chain.append(parent)
        if len(chain) != len(records):
            raise ValueError('ownership cycle')
        return chain

    def reconcile(self):
        """Validate every publication chain; return the exact committed heads."""
        import gitpickle_archive as a
        heads = {}
        try:
            self.ownership()
            for path in (self.root / 'repos').glob('*'):
                chain = a.inventory(self.root, path.name)
                heads[path.name] = chain[-1] if chain else None
            return heads
        except (ValueError, OSError, KeyError):
            self._stopped = True
            raise

    @property
    def writable(self):
        import gitpickle_archive as a
        if self._lock is None or self._stopped or self.owner_key is None:
            return False
        try:
            chain = self.ownership()
        except (ValueError, OSError, KeyError):
            self._stopped = True
            raise
        return (bool(chain) and chain[-1] == self.owner_key and
                a.read(self.root, self.owner_key, 'writer')['server_id'] == self.server_id)

    def _publish_owner(self, parent, fencing, heads):
        import gitpickle_archive as a
        return a.record(self.root, 'ownership', {
            'kind': 'writer', 'archive_id': a.read(self.root, 'archive.json', 'archive')['archive_id'],
            'server_id': self.server_id, 'parent': parent, 'fencing': fencing, 'heads': heads})

    def takeover(self, previous_owner, fencing_evidence, reconciled_heads):
        """Record explicit operator fencing evidence after matching all heads.

        Evidence must identify how old credentials/access were revoked. This is
        an operator attestation, not proof supplied by the local process lock.
        """
        with self._mutex:
            if self._lock is None or self._stopped or self._active:
                raise PermissionError('writer session is closed or stopped')
            chain = self.ownership()
            current = chain[-1] if chain else None
            if current != previous_owner:
                raise ValueError('ownership changed; reconcile again')
            if not isinstance(fencing_evidence, str) or not fencing_evidence.strip():
                raise PermissionError('explicit external fencing evidence required')
            heads = self.reconcile()
            if heads != reconciled_heads:
                raise ValueError('committed heads changed; reconcile again')
            self.owner_key = self._publish_owner(current, fencing_evidence, heads)

    @contextmanager
    def mutation(self, repo_id):
        with self._mutex:
            if not self.writable:
                raise PermissionError('archive is read-only; fence and reconcile before takeover')
            lock = self._repositories.setdefault(repo_id, threading.Lock())
        with lock:
            with self._mutex:
                if not self.writable:
                    raise PermissionError('archive mutation stopped')
                self.reconcile()
                self._active += 1
            try:
                yield
            finally:
                with self._mutex:
                    self._active -= 1

    def capture(self, repo, repo_id, source, lfs=None, operation_id=None, wiki=None):
        import gitpickle_archive as a
        with self.mutation(repo_id):
            return a._capture(self.root, repo, repo_id, source, lfs, self.owner_key, operation_id, wiki)
