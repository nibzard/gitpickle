"""Durable job service for the archive reference implementation.

One service owns one Writer session. Clients only submit, inspect, or follow jobs.
Archive records are authoritative; SQLite and worker state are disposable.
"""
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import subprocess
import threading
import time

import gitpickle_archive as a
from gitpickle_writer import Writer


class JobError(Exception):
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class Cancelled(Exception):
    pass


def boundary(name):
    """Fault injection point, mapped to JobReplay.tla in docs/jobs.md."""


class Jobs:
    def __init__(self, root, *, concurrency=4, source_interval=1.0,
                 disk_reserve=256 * 1024 * 1024, max_attempts=8,
                 backoff_base=1.0, backoff_cap=3600.0,
                 clock=time.time, random_fraction=random.random, disk_free=None, source_adapter=None, writer=None):
        if (concurrency < 1 or source_interval < 0 or disk_reserve < 0
                or max_attempts < 1 or backoff_base <= 0 or backoff_cap < backoff_base):
            raise ValueError('invalid scheduler limits')
        self.source_adapter = source_adapter
        self.root = Path(root).resolve()
        self.limit, self.interval, self.reserve = concurrency, source_interval, disk_reserve
        self.max_attempts, self.base, self.cap = max_attempts, backoff_base, backoff_cap
        self.clock, self.random = clock, random_fraction
        self.disk_free = disk_free or (lambda: shutil.disk_usage(self.root).free)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.running, self.order, self.source_next = {}, deque(), {}
        self.writer = writer
        self.owns_writer = writer is None
        self.db = None
        self.pool = None
        self.thread = None
        self.stopping = False
        self.failure = None
        self.dirty = False

    def __enter__(self):
        if self.owns_writer:
            self.writer = Writer(self.root).__enter__()
        try:
            if not self.writer.writable or self.writer.root != self.root:
                raise PermissionError('archive is read-only')
            self.archive_id = a.read(self.root, 'archive.json', 'archive')['archive_id']
            cache = self.root / '.projection'
            cache.mkdir(exist_ok=True)
            # Recreate even a corrupt SQLite file. Never recover authority from it.
            path = cache / 'jobs.sqlite3'
            for suffix in ('', '-journal', '-wal', '-shm'):
                Path(str(path) + suffix).unlink(missing_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, state TEXT, body TEXT)')
            self.jobs = {}
            self._rebuild()
            self.pool = ThreadPoolExecutor(max_workers=self.limit, thread_name_prefix='gitpickle-job')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        with self.lock:
            self.stopping = True
            self.changed.notify_all()
        if self.thread:
            self.thread.join()
        try:
            if self.pool:
                self.pool.shutdown(wait=True)
                # Persist completed work before releasing archive ownership.
                self.tick()
        finally:
            if self.db:
                self.db.close()
            if self.writer and self.owns_writer:
                self.writer.__exit__(None, None, None)

    def _rebuild(self):
        jobs = {}
        for directory in (self.root / 'jobs').glob('*'):
            records, children = {}, {}
            for path in (directory / 'events').glob('*'):
                key = str(path.relative_to(self.root))
                record = a.read(self.root, key, 'job')
                if record['archive_id'] != self.archive_id or record['job_id'] != directory.name:
                    raise ValueError('job identity mismatch')
                parent = record['parent']
                if parent in children:
                    raise ValueError('conflicting job records')
                children[parent] = key
                records[key] = record
            if not records:
                continue  # An interrupted first write can leave empty directories.
            parent, count, state = None, 0, None
            while parent in children:
                parent = children[parent]
                record = records[parent]
                if state is None:
                    if record['event'] != 'accepted':
                        raise ValueError('job has no acceptance')
                    state = record['value']
                    request = state['request']
                    expected = self.identity(request['actor'], request['operation'], request['key'])
                    if expected != directory.name:
                        raise ValueError('job request identity mismatch')
                else:
                    if record['event'] == 'accepted' or state['state'] in ('completed', 'failed', 'cancelled'):
                        raise ValueError('invalid job transition')
                    # Requests cannot change after acceptance.
                    if record['value']['request'] != state['request']:
                        raise ValueError('job arguments changed')
                    state = record['value']
                count += 1
            if count != len(records) or not state:
                raise ValueError('broken job record chain')
            state['head'] = parent
            jobs[directory.name] = state
        self.jobs = jobs
        for job in jobs.values():
            a.durable_put(self.root, job['head'], a.read(self.root, job['head']))
            self._project(job)
        self.dirty = False
        for job in sorted(jobs.values(), key=lambda j: (j['sequence'], j['id'])):
            repository = job['request']['repository']
            if repository not in self.order:
                self.order.append(repository)
        # Retain source cooldowns even after local disk loss.
        for job in jobs.values():
            source = job['request']['source']
            self.source_next[source] = max(self.source_next.get(source, 0), job['source_next'])

    def _project(self, job):
        self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)',
                        (job['id'], job['state'], json.dumps(job, sort_keys=True)))
        self.db.commit()

    def _append(self, job, event):
        if not self.writer.writable:
            raise PermissionError('archive mutation stopped')
        value = copy.deepcopy(job)
        parent = value.pop('head', None)
        self.dirty = True
        boundary('before_' + event)
        key = a.record(self.root, f"jobs/{job['id']}/events", {
            'kind': 'job', 'archive_id': self.archive_id, 'job_id': job['id'],
            'parent': parent, 'event': event, 'value': value})
        # Readback and directory flush precede every acknowledgment.
        job['head'] = key
        self.jobs[job['id']] = job
        boundary('after_' + event)
        self._project(job)
        self.dirty = False
        self.changed.notify_all()

    @staticmethod
    def identity(actor, operation, key):
        return a.digest(a.encode({'actor': actor, 'operation': operation, 'key': key}))

    def accept(self, actor, operation, key, repository, source, arguments, *, staging_bytes=0):
        if (operation not in ('backup', 'sync') or not all(isinstance(s, str) and 0 < len(s) <= 256
                for s in (actor, key, source)) or not re.fullmatch('[A-Za-z0-9_-]+', repository)
                or not isinstance(staging_bytes, int) or staging_bytes < 0
                or not isinstance(arguments, dict)
                or not ((set(arguments) == {'repo'} and isinstance(arguments['repo'], str)
                         and arguments['repo'])
                        or (set(arguments) == {'github_id'}
                            and type(arguments['github_id']) is int and arguments['github_id'] > 0
                            and source == 'github' and repository == 'github-' + str(arguments['github_id'])))):
            raise JobError('invalid_input')
        if 'repo' in arguments and (':' in arguments['repo'] or not Path(arguments['repo']).is_dir()):
            raise JobError('invalid_input')
        source = a.security.source_label(source)
        # The reference capture adapter accepts local paths, not credential-bearing URLs.
        arguments = ({'repo': str(Path(arguments['repo']).resolve())} if 'repo' in arguments
                     else dict(arguments))
        request = {'actor': actor, 'operation': operation, 'key': key,
                   'repository': repository, 'source': source,
                   'arguments': arguments, 'staging_bytes': staging_bytes}
        job_id = self.identity(actor, operation, key)
        with self.lock:
            if self.stopping:
                raise JobError('unavailable')
            # Also reconciles acceptance when the previous reply was lost.
            if self.dirty:
                self._rebuild()
            if job_id in self.jobs:
                job = self.jobs[job_id]
                if job['request'] != request:
                    raise JobError('conflict')
                a.durable_put(self.root, job['head'], a.read(self.root, job['head']))
                return copy.deepcopy(job)
            job = {'id': job_id, 'request': request, 'state': 'queued',
                   'sequence': max((j['sequence'] for j in self.jobs.values()), default=0) + 1,
                   'accepted_at': self.clock(), 'attempts': 0, 'ready_at': 0,
                   'source_next': 0, 'cancel_requested': False, 'result': None, 'error': None}
            self._append(job, 'accepted')
            if repository not in self.order:
                self.order.append(repository)
            return copy.deepcopy(job)

    def inspect(self, job_id):
        with self.lock:
            if self.dirty:
                self._rebuild()
            return copy.deepcopy(self.jobs[job_id])

    def cancel(self, job_id):
        with self.lock:
            if self.dirty:
                self._rebuild()
            job = copy.deepcopy(self.jobs[job_id])
            if job['state'] in ('completed', 'failed', 'cancelled') or job['cancel_requested']:
                return copy.deepcopy(job)
            job['cancel_requested'] = True
            # Persist intent. Only a worker at a safe boundary makes it terminal.
            self._append(job, 'cancel_requested')
            return copy.deepcopy(job)

    def checkpoint(self, job_id):
        with self.lock:
            if self.jobs[job_id]['cancel_requested']:
                raise Cancelled()

    def _execute(self, job):
        request = job['request']
        # A committed capture wins over cancellation, including after a crash.
        point = (a.reconcile_capture(self.root, request['repository'], request['source'], job['id'])
                 if request['operation'] == 'backup' else None)
        if point is not None:
            return point
        self.checkpoint(job['id'])
        from gitpickle_hosted import state
        if state(self.root, request['repository'])[1]['mode'] == 'hosted':
            raise JobError('source_updates_paused')
        if 'github_id' in request['arguments']:
            if self.source_adapter is None:
                raise JobError('authentication')
            return self.source_adapter.execute(self, job)
        if request['operation'] == 'sync':
            return self.observe(job, request['arguments']['repo'])
        return self.writer.capture(request['arguments']['repo'], request['repository'],
                                   request['source'], operation_id=job['id'])

    def observe(self, job, repo):
        """Refresh source refs without publishing or deleting retained history."""
        from gitpickle_hosted import state
        with self.writer.mutation(job['request']['repository']):
            if state(self.root, job['request']['repository'])[1]['mode'] == 'hosted':
                raise JobError('source_updates_paused')
            refs = a.git(repo, 'for-each-ref', '--format=%(refname) %(objectname)').decode()
            try:
                head = {'symbolic': a.git(repo, 'symbolic-ref', 'HEAD').decode().strip()}
            except subprocess.CalledProcessError:
                head = {'detached': a.git(repo, 'rev-parse', 'HEAD').decode().strip()}
            return a.record(self.root, 'control', {
                'kind': 'control', 'archive_id': self.archive_id,
                'operation_id': job['id'], 'record_type': 'source_tracking',
                'recorded_at': a.timestamp(), 'predecessor': None,
                'payload': {'repository_id': job['request']['repository'], 'refs': refs,
                            'head': head, 'components': ['git']}})

    def _finish(self, job_id, future):
        job = copy.deepcopy(self.jobs[job_id])
        if job['state'] in ('completed', 'failed', 'cancelled'):
            return
        try:
            job['result'] = future.result()
            job['state'], job['error'] = 'completed', None
        except Cancelled:
            job['state'] = 'cancelled'
        except Exception as error:
            # Do not archive exception strings: Git diagnostics can contain secrets.
            if isinstance(error, JobError):
                code = error.code
            elif isinstance(error, PermissionError):
                code = 'forbidden'
            elif isinstance(error, ValueError):
                code = 'integrity_failure'
            elif isinstance(error, subprocess.CalledProcessError):
                code = 'source_unavailable'
            elif isinstance(error, OSError):
                code = 'storage_unavailable'
            else:
                code = 'operational_failure'
            retryable = code in ('rate_limited', 'storage_unavailable', 'source_unavailable', 'operational_failure')
            job['error'] = code
            if code in ('authentication', 'forbidden', 'source_unavailable'):
                a.record(self.root, 'control', {'kind': 'control', 'archive_id': self.archive_id,
                    'operation_id': job_id, 'record_type': 'source_failure',
                    'recorded_at': a.timestamp(), 'predecessor': None,
                    'payload': {'repository_id': job['request']['repository'], 'code': code}})
            if retryable and job['attempts'] < self.max_attempts:
                delay = min(self.cap, self.base * 2 ** min(job['attempts'] - 1, 30))
                delay *= 0.5 + 0.5 * self.random()
                if isinstance(error, JobError) and error.retry_after is not None:
                    delay = max(delay, error.retry_after)
                job['ready_at'] = self.clock() + delay
                job['state'] = 'queued'
                if code == 'rate_limited':
                    job['source_next'] = job['ready_at']
                    self.source_next[job['request']['source']] = max(
                        self.source_next.get(job['request']['source'], 0), job['ready_at'])
            else:
                job['state'] = 'failed'
        self._append(job, 'retry' if job['state'] == 'queued' else 'terminal')

    def tick(self):
        """Reap outcomes and admit at most the configured number of workers."""
        with self.lock:
            if self.dirty:
                self._rebuild()
            for job_id, future in list(self.running.items()):
                if future.done():
                    self._finish(job_id, future)
                    del self.running[job_id]
            if self.stopping:
                return
            active_repos = {self.jobs[j]['request']['repository'] for j in self.running}
            reserved = sum(self.jobs[j]['request']['staging_bytes'] for j in self.running)
            free = self.disk_free()
            for _ in range(len(self.order)):
                if len(self.running) >= self.limit:
                    break
                repository = self.order.popleft()
                self.order.append(repository)
                if repository in active_repos:
                    continue
                candidates = [j for j in self.jobs.values() if j['request']['repository'] == repository
                              and j['id'] not in self.running and j['state'] in ('queued', 'running')
                              and j['ready_at'] <= self.clock()]
                if not candidates:
                    continue
                job = copy.deepcopy(min(candidates, key=lambda j: (j['sequence'], j['id'])))
                source = job['request']['source']
                # A previous attempt may have published before losing its result.
                # Reconcile without source access or another staging reservation.
                if job['attempts'] and job['request']['operation'] == 'backup':
                    try:
                        point = a.reconcile_capture(self.root, repository, source, job['id'])
                    except Exception as error:
                        # Preserve the worker retry/error policy for archive read failures.
                        job['state'], job['attempts'] = 'running', job['attempts'] + 1
                        self._append(job, 'started')
                        outcome = Future()
                        outcome.set_exception(error)
                        self._finish(job['id'], outcome)
                        continue
                    if point is not None:
                        job['state'], job['result'], job['error'] = 'completed', point, None
                        self._append(job, 'terminal')
                        continue
                # Cancellation needs no staging capacity.
                if not job['cancel_requested']:
                    if self.source_next.get(source, 0) > self.clock():
                        continue
                    if free - reserved - job['request']['staging_bytes'] < self.reserve:
                        continue
                job['state'] = 'running'
                job['attempts'] += 1
                job['source_next'] = self.clock() + self.interval
                self._append(job, 'started')
                self.source_next[source] = job['source_next']
                reserved += job['request']['staging_bytes']
                active_repos.add(repository)
                self.running[job['id']] = self.pool.submit(self._execute, copy.deepcopy(job))

    def start(self):
        """Start the server loop. A client disconnect does not own this thread."""
        with self.lock:
            if self.thread is not None:
                return
            def loop():
                while True:
                    with self.changed:
                        if self.stopping:
                            return
                        try:
                            self.tick()
                        except Exception as error:
                            self.failure = error
                            self.stopping = True
                            self.changed.notify_all()
                            return
                        self.changed.wait(timeout=0.05)
            self.thread = threading.Thread(target=loop, name='gitpickle-scheduler')
            self.thread.start()

    def _client_wait(self):
        self.changed.wait(timeout=0.1)

    def follow(self, job_id):
        """Follow from a client thread. Ctrl-C detaches without cancellation."""
        try:
            with self.changed:
                while True:
                    job = self.inspect(job_id)
                    if job['state'] in ('completed', 'failed', 'cancelled'):
                        return job
                    if self.failure is not None:
                        raise JobError('storage_unavailable') from None
                    self._client_wait()
        except KeyboardInterrupt:
            return {'id': job_id, 'state': 'detached', 'message': 'Server work continues.'}
