"""Durable scheduling acceptance, resource bounds, and capture replay tests."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_jobs as j


class Crash(BaseException):
    pass


class JobsTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'jobs') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'jobs-test')
        self.repo = self.root / 'source.git'
        self.repo.mkdir()
        a.git(self.repo, 'init', '--bare', '--template=')
        self.now = 100.0

    def service(self, **kwargs):
        return j.Jobs(self.store, clock=lambda: self.now, disk_reserve=10,
                      source_interval=0, **kwargs)

    def accept(self, service, key='k', repository='r', source='github', actor='actor',
               operation='backup', **kwargs):
        return service.accept(actor, operation, key, repository, source,
                              {'repo': str(self.repo)}, **kwargs)['id']

    def drain(self, service, ids, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            service.tick()
            if all(service.inspect(i)['state'] in ('completed', 'failed', 'cancelled') for i in ids):
                return
            time.sleep(0.005)
        self.fail('jobs did not reach terminal outcomes')

    def test_duplicate_requests_scope_and_argument_conflict(self):
        with self.service() as service:
            with ThreadPoolExecutor(max_workers=12) as clients:
                ids = list(clients.map(lambda _: self.accept(service), range(30)))
            self.assertEqual(1, len(set(ids)))
            self.assertNotEqual(ids[0], self.accept(service, actor='other'))
            self.assertNotEqual(ids[0], self.accept(service, operation='sync'))
            with self.assertRaisesRegex(j.JobError, 'conflict'):
                self.accept(service, repository='other')
            self.assertEqual(3, len(list((self.store / 'jobs').glob('*/events/*'))))

    def test_accept_and_terminal_crash_boundaries_and_sqlite_loss(self):
        for fault in ('before_accepted', 'after_accepted', 'before_terminal', 'after_terminal'):
            with self.subTest(fault=fault):
                self.store = self.root / fault
                a.initialize(self.store, 'jobs-test')
                def crash(name):
                    if name == fault:
                        raise Crash(name)
                with self.service() as service:
                    if 'accepted' in fault:
                        with patch.object(j, 'boundary', crash), self.assertRaises(Crash):
                            self.accept(service)
                    else:
                        job_id = self.accept(service)
                        service.tick()
                        service.running[job_id].result(timeout=10)
                        with patch.object(j, 'boundary', crash), self.assertRaises(Crash):
                            service.tick()
                shutil.rmtree(self.store / '.projection')
                with self.service() as service:
                    job_id = self.accept(service)
                    self.drain(service, [job_id])
                    self.assertEqual('completed', service.inspect(job_id)['state'])
                    self.assertEqual(1, len(a.inventory(self.store, 'r')))
                    rows = service.db.execute('SELECT state FROM jobs').fetchall()
                    self.assertEqual([('completed',)], rows)

    def test_lost_write_acknowledgments(self):
        original = a.put
        def lost(*args):
            original(*args)
            raise TimeoutError('lost acknowledgment')
        with self.service() as service, patch.object(a, 'put', lost):
            job_id = self.accept(service)
            self.drain(service, [job_id])
            self.assertEqual('completed', service.inspect(job_id)['state'])
            self.assertEqual(job_id, self.accept(service))

    def test_interrupted_capture_reconciles_before_cancellation(self):
        with self.service() as service:
            job_id = self.accept(service)
            def crash(name):
                if name == 'after_publication':
                    raise Crash(name)
            with patch.object(a, 'boundary', crash):
                service.tick()
                with self.assertRaises(Crash):
                    service.running[job_id].result(timeout=10)
            # Simulate process death: discard this future, retain durable records.
            service.running.clear()
        shutil.rmtree(self.store / '.projection')
        shutil.rmtree(self.repo)
        with self.service() as service:
            service.cancel(job_id)
            self.drain(service, [job_id])
            self.assertEqual('completed', service.inspect(job_id)['state'])
            self.assertEqual(1, len(a.inventory(self.store, 'r')))

    def test_bounds_serialization_disk_and_fairness(self):
        gate = threading.Event()
        started = []
        def execute(job):
            started.append(job['request']['repository'])
            if not gate.wait(5):
                raise RuntimeError('test gate timed out')
            return 'point'
        with self.service(concurrency=2, disk_free=lambda: 40) as service:
            ids = [self.accept(service, key=str(i), staging_bytes=15) for i in range(12)]
            ids += [self.accept(service, key='small', repository='small', staging_bytes=15),
                    self.accept(service, key='last', repository='last', staging_bytes=15)]
            with patch.object(service, '_execute', execute):
                service.tick()
                self.assertEqual(2, len(service.running))
                self.assertEqual({'r', 'small'}, {service.jobs[i]['request']['repository'] for i in service.running})
                for _ in range(5):
                    service.tick()
                    self.assertEqual(2, len(service.running))
                gate.set()
                self.drain(service, ids)
                self.assertIn('last', started[:3])
            self.assertTrue(all(service.inspect(i)['state'] == 'completed' for i in ids))

    def test_low_disk_queues_without_losing_acceptance(self):
        free = [9]
        with self.service(disk_free=lambda: free[0]) as service:
            job_id = self.accept(service)
            service.tick()
            self.assertEqual({}, service.running)
            self.assertEqual('queued', service.inspect(job_id)['state'])
            free[0] = 100
            self.drain(service, [job_id])

    def test_retry_rate_limit_and_restart_preserve_delay(self):
        with self.service(random_fraction=lambda: 0.25) as service:
            job_id = self.accept(service)
            with patch.object(service, '_execute', side_effect=j.JobError('rate_limited', 20)):
                service.tick()
                with self.assertRaises(j.JobError):
                    service.running[job_id].result(timeout=5)
                service.tick()
            job = service.inspect(job_id)
            self.assertEqual(120, job['ready_at'])
            self.assertEqual('queued', job['state'])
        shutil.rmtree(self.store / '.projection')
        with self.service() as service:
            other = self.accept(service, key='other', repository='other')
            service.tick()
            self.assertEqual({}, service.running)
            self.now = 120
            self.drain(service, [job_id, other])

    def test_exponential_jitter_and_attempt_bound(self):
        with self.service(max_attempts=3, random_fraction=lambda: 0.0) as service:
            job_id = self.accept(service)
            with patch.object(service, '_execute', side_effect=j.JobError('storage_unavailable')):
                for delay in (0.5, 1.0):
                    service.tick()
                    with self.assertRaises(j.JobError):
                        service.running[job_id].result(timeout=5)
                    service.tick()
                    self.assertEqual(self.now + delay, service.inspect(job_id)['ready_at'])
                    self.now += delay
                self.drain(service, [job_id])
            self.assertEqual('failed', service.inspect(job_id)['state'])
            self.assertEqual(3, service.inspect(job_id)['attempts'])

    def test_source_interval_does_not_block_other_sources(self):
        with j.Jobs(self.store, source_interval=10, disk_reserve=0,
                    clock=lambda: self.now) as service:
            first = self.accept(service)
            second = self.accept(service, key='second', repository='second')
            other = self.accept(service, key='third', repository='third', source='other')
            service.tick()
            self.assertEqual({first, other}, set(service.running))
            service.running[first].result(timeout=5)
            service.running[other].result(timeout=5)
            service.tick()
            self.assertEqual({}, service.running)
            self.now += 10
            self.drain(service, [second])

    def test_queued_and_running_cancel_at_safe_boundary(self):
        with self.service() as service:
            queued = self.accept(service)
            service.cancel(queued)
            self.drain(service, [queued])
            self.assertEqual('cancelled', service.inspect(queued)['state'])
            self.assertEqual([], a.inventory(self.store, 'r'))
            entered, release = threading.Event(), threading.Event()
            def execute(job):
                entered.set()
                release.wait(5)
                service.checkpoint(job['id'])
                self.fail('cancelled work crossed the safe boundary')
            running = self.accept(service, key='running')
            with patch.object(service, '_execute', execute):
                service.tick()
                self.assertTrue(entered.wait(5))
                service.cancel(running)
                release.set()
                self.drain(service, [running])
            self.assertEqual('cancelled', service.inspect(running)['state'])

    def test_client_interrupt_detaches_server_work(self):
        entered, release = threading.Event(), threading.Event()
        def execute(job):
            entered.set()
            release.wait(5)
            return 'committed-point'
        with self.service() as service, patch.object(service, '_execute', execute):
            job_id = self.accept(service)
            service.start()
            self.assertTrue(entered.wait(5))
            with patch.object(service, '_client_wait', side_effect=KeyboardInterrupt):
                self.assertEqual('detached', service.follow(job_id)['state'])
            self.assertFalse(service.inspect(job_id)['cancel_requested'])
            release.set()
            self.assertEqual('completed', service.follow(job_id)['state'])
            service.cancel(job_id)
            self.assertEqual('completed', service.inspect(job_id)['state'])

    def test_many_small_repositories_are_not_starved(self):
        with self.service(concurrency=4) as service:
            ids = [self.accept(service, str(i), 'large') for i in range(10)]
            ids += [self.accept(service, 'small-key' + str(i), 'small' + str(i)) for i in range(403)]
            started = []
            def execute(job):
                started.append(job['request']['repository'])
                return 'point'
            with patch.object(service, '_execute', execute):
                self.drain(service, ids, timeout=60)
            self.assertEqual(404, len(set(started[:404])))

    def test_interrupted_initial_write_leaves_no_accepted_job(self):
        with self.service() as service:
            original = a.put
            def fail(root, key, data):
                if key.startswith('jobs/'):
                    (root / key).parent.mkdir(parents=True, exist_ok=True)
                    raise OSError('disk unavailable before linking acceptance')
                return original(root, key, data)
            with patch.object(a, 'put', fail), self.assertRaises(OSError):
                self.accept(service)
        with self.service() as service:
            self.assertEqual({}, service.jobs)
            job_id = self.accept(service)
            self.drain(service, [job_id])

    def test_failed_terminal_flush_cannot_acknowledge_completion(self):
        with self.service() as service:
            job_id = self.accept(service)
            service.tick()
            service.running[job_id].result(timeout=5)
            original = a.put
            def fail(root, key, data):
                original(root, key, data)
                if key.startswith('jobs/'):
                    raise OSError('simulated directory flush failure')
            with patch.object(a, 'put', fail):
                with self.assertRaises(OSError):
                    service.tick()
                with self.assertRaises(OSError):
                    service.inspect(job_id)
            # A successful reflush now permits a terminal acknowledgment.
            service.tick()
            self.assertEqual('completed', service.inspect(job_id)['state'])
            self.assertEqual(1, len(a.inventory(self.store, 'r')))

    def test_real_process_exit_after_acceptance_and_capture(self):
        for fault in ('after_accepted', 'after_publication', 'after_terminal'):
            with self.subTest(fault=fault):
                self.store = self.root / ('process-' + fault)
                a.initialize(self.store, 'jobs-test')
                script = """
import os, sys, time
from pathlib import Path
import gitpickle_jobs as j
import gitpickle_archive as a
root, repo, fault = sys.argv[1:]
def crash(name):
    if name == fault:
        os._exit(71)
j.boundary = crash
a.boundary = crash
with j.Jobs(root, disk_reserve=0, source_interval=0, clock=lambda: 100.0) as service:
    job = service.accept('actor', 'backup', 'k', 'r', 'github', {'repo': repo})
    while True:
        service.tick()
        time.sleep(0.005)
"""
                env = dict(os.environ, PYTHONPATH=str(Path('tools').resolve()),
                           PYTHONDONTWRITEBYTECODE='1')
                result = subprocess.run([sys.executable, '-c', script, str(self.store),
                                         str(self.repo), fault], env=env, timeout=15,
                                        capture_output=True)
                self.assertEqual(71, result.returncode, result.stderr.decode())
                shutil.rmtree(self.store / '.projection')
                with self.service() as service:
                    job_id = self.accept(service)
                    self.drain(service, [job_id])
                    self.assertEqual('completed', service.inspect(job_id)['state'])
                    self.assertEqual(1, len(a.inventory(self.store, 'r')))

    def test_actual_captures_on_concurrent_repositories(self):
        with self.service(concurrency=3) as service:
            ids = [self.accept(service, str(i), 'r' + str(i)) for i in range(6)]
            self.drain(service, ids)
            for index, job_id in enumerate(ids):
                self.assertEqual('completed', service.inspect(job_id)['state'])
                self.assertEqual(1, len(a.inventory(self.store, 'r' + str(index))))

    def test_terminal_failure_is_durable_and_diagnostics_are_redacted(self):
        with self.service() as service:
            job_id = self.accept(service)
            with patch.object(service, '_execute', side_effect=j.JobError('authentication')):
                self.drain(service, [job_id])
            self.assertEqual('authentication', service.inspect(job_id)['error'])
        with self.service() as service:
            self.assertEqual('failed', service.inspect(job_id)['state'])
            self.assertEqual(1, service.inspect(job_id)['attempts'])

    def test_corrupt_projection_is_rebuilt_and_archive_forks_fail_closed(self):
        with self.service() as service:
            job_id = self.accept(service)
            job = service.inspect(job_id)
        (self.store / '.projection/jobs.sqlite3').write_bytes(b'not sqlite')
        with self.service() as service:
            self.assertEqual('queued', service.inspect(job_id)['state'])
        value = dict(job)
        value.pop('head')
        value['state'] = 'failed'
        # Two children of acceptance represent a conflicting durable history.
        for event in ('terminal', 'retry'):
            a.record(self.store, f'jobs/{job_id}/events', {'kind': 'job', 'archive_id': 'jobs-test',
                'job_id': job_id, 'parent': job['head'], 'event': event, 'value': value})
        with self.assertRaisesRegex(ValueError, 'conflicting job records'):
            with self.service():
                pass


if __name__ == '__main__':
    unittest.main()
