"""Real Git clients exercise the isolated final-response coordinator."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest

import gitpickle_archive as archive
import gitpickle_engine_spike as native
import gitpickle_perf as fixtures
import gitpickle_publish_spike as probe
import gitpickle_security as security


@contextmanager
def endpoint(coordinator):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != '/r.git/info/refs?service=git-receive-pack':
                self.send_error(404)
                return
            with coordinator.lock, tempfile.TemporaryDirectory(dir=coordinator.work) as temp:
                repo = Path(temp) / 'advertise.git'
                archive.restore(coordinator.store, coordinator.point(), repo)
                adv = native.git(repo, 'receive-pack', '--stateless-rpc', '--advertise-refs', str(repo)).stdout
            if getattr(coordinator, 'plain_status', False):
                size = int(adv[:4], 16)
                first = adv[4:size].replace(b' side-band-64k', b'').replace(b' report-status-v2', b'')
                adv = probe.packet(first) + adv[size:]
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-git-receive-pack-advertisement')
            self.end_headers()
            self.wfile.write(probe.packet(b'# service=git-receive-pack\n') + b'0000' + adv)

        def do_POST(self):
            if self.path != '/r.git/git-receive-pack' or self.headers.get('Transfer-Encoding'):
                self.send_error(400)
                return
            request = self.rfile.read(int(self.headers['Content-Length']))
            coordinator.last_request = request
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-git-receive-pack-result')
            self.end_headers()
            def send(data):
                self.wfile.write(data)
                self.wfile.flush()
            try:
                coordinator.receive(request, send)
            except (ValueError, OSError, RuntimeError):
                # Git needs an explicit fatal packet after streamed progress.
                # Never substitute fabricated per-ref success or rejection.
                first = request[4:int(request[:4], 16)]
                capabilities = first.split(b'\0', 1)[-1].split()
                fatal = b'\x03' if b'side-band-64k' in capabilities else b'ERR '
                send(probe.packet(fatal + b'publication failed; outcome unknown\n') + b'0000')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/r.git'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated/publication')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix=os.getenv('LOOPER_RESOURCE_PREFIX', 'publish') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source.git'
        fixtures.make_repo(self.source, 8, 2, 1024)
        self.store = self.root / 'archive'
        archive.initialize(self.store, 'publication-probe')
        archive.capture(self.store, self.source, 'r', 'hosted:probe')
        self.hooks = self.root / 'hooks'
        self.hooks.mkdir()
        self.hook('post-receive', 'echo PROGRESS_BEFORE_DURABILITY >&2\n')
        self.coordinator = probe.Coordinator(self.store, self.root / 'work', hooks=self.hooks)
        commit = native.git(self.source, 'commit-tree', 'main^{tree}', '-p', 'main',
                            data=b'new pushed commit\n').stdout.decode().strip()
        native.git(self.source, 'update-ref', 'refs/heads/accepted', commit)
        native.git(self.source, 'update-ref', 'refs/heads/rejected', 'main')

    def hook(self, name, body):
        path = self.hooks / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o700)

    def push(self, url, *args):
        return security.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(self.source),
                             'push', '--porcelain', url, *args],
                            env=native.environment(), timeout=20, check=False)

    def refs(self):
        manifest = archive.read(self.store, self.coordinator.point(), 'point')
        return manifest['refs']

    def test_atomic_and_mixed_results_preserve_native_status(self):
        self.hook('update', 'test "$1" != refs/heads/rejected\n')
        with endpoint(self.coordinator) as url:
            before = self.coordinator.point()
            atomic = self.push(url, '--atomic', 'accepted', 'rejected')
            self.assertNotEqual(0, atomic.returncode)
            self.assertIn(b'atomic push failure', atomic.stdout + atomic.stderr)
            self.assertEqual(before, self.coordinator.point())
            mixed = self.push(url, 'accepted', 'rejected')
            self.assertNotEqual(0, mixed.returncode)
            self.assertIn(b'[new branch]', mixed.stdout)
            self.assertIn(b'[remote rejected]', mixed.stdout)
            self.assertIn('refs/heads/accepted', self.refs())
            self.assertNotIn('refs/heads/rejected', self.refs())
            deleted = self.push(url, ':accepted')
            self.assertEqual(0, deleted.returncode, deleted.stderr)
            self.assertNotIn('refs/heads/accepted', self.refs())
            native.git(self.source, 'update-ref', 'refs/heads/second', 'accepted')
            success = self.push(url, '--atomic', 'accepted', 'second')
            self.assertEqual(0, success.returncode, success.stderr)
            self.assertIn('refs/heads/second', self.refs())

    def test_progress_streams_while_final_success_waits(self):
        reached, release, progress = threading.Event(), threading.Event(), threading.Event()
        def boundary(name):
            if name == 'before_publication':
                reached.set()
                if not release.wait(10):
                    raise RuntimeError('test release timeout')
        self.coordinator.boundary = boundary
        before = self.coordinator.point()
        with endpoint(self.coordinator) as url:
            client = subprocess.Popen(['git', '-C', str(self.source), 'push', url, 'accepted'],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=native.environment(),
                                      start_new_session=True)
            def cleanup_client():
                try:
                    os.killpg(client.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                client.wait()
            self.addCleanup(cleanup_client)
            errors = bytearray()
            def read_progress():
                while data := client.stderr.read(1):
                    errors.extend(data)
                    if b'PROGRESS_BEFORE_DURABILITY' in errors:
                        progress.set()
            reader = threading.Thread(target=read_progress)
            reader.start()
            try:
                self.assertTrue(reached.wait(10))
                self.assertTrue(progress.wait(5), errors)
                self.assertIsNone(client.poll())
                self.assertEqual(before, self.coordinator.point())
            finally:
                release.set()
            self.assertEqual(0, client.wait(timeout=20), errors)
            reader.join(5)
            client.stdout.close()
            client.stderr.close()
        self.assertIn('refs/heads/accepted', self.refs())
        # Delete all engine work and source data. Only archive data can restore success.
        shutil.rmtree(self.source)
        shutil.rmtree(self.coordinator.work)
        destination = self.root / 'recovered.git'
        archive.restore(self.store, self.coordinator.point(), destination)
        native.git(destination, 'fsck', '--full', '--strict')
        self.assertIn('refs/heads/accepted', dict(line.split(' ', 1) for line in fixtures.refs(destination)))

    def test_failure_and_response_loss_at_every_coordinator_boundary(self):
        for fault in ['staged', 'native_complete', 'before_publication', 'after_publication', 'before_final']:
            with self.subTest(fault=fault):
                ref = 'ref-' + fault.replace('_', '-')
                native.git(self.source, 'update-ref', 'refs/heads/' + ref, 'main')
                before = self.coordinator.point()
                def fail(name):
                    if name == fault:
                        raise OSError('injected crash or storage outage')
                self.coordinator.boundary = fail
                with endpoint(self.coordinator) as url:
                    result = self.push(url, ref)
                    self.assertNotEqual(0, result.returncode)
                    committed = fault in ['after_publication', 'before_final']
                    self.assertEqual(committed, 'refs/heads/' + ref in self.refs())
                    if not committed:
                        self.assertEqual(before, self.coordinator.point())
                    self.coordinator.boundary = lambda name: None
                    retry = self.push(url, ref)
                    self.assertEqual(0, retry.returncode, retry.stderr)
                    if committed:
                        self.assertIn(b'[up to date]', retry.stdout)
                    self.assertIn('refs/heads/' + ref, self.refs())
                shutil.rmtree(self.coordinator.work)
                self.coordinator.work.mkdir()
                archive.restore(self.store, self.coordinator.point(), self.root / ref)

    def test_native_report_status_without_sideband(self):
        self.coordinator.plain_status = True
        with endpoint(self.coordinator) as url:
            result = self.push(url, 'accepted')
            self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('refs/heads/accepted', self.refs())
        def fail(name):
            if name == 'before_publication':
                raise OSError('storage unavailable')
        self.coordinator.boundary = fail
        with endpoint(self.coordinator) as url:
            failed = self.push(url, 'rejected')
            self.assertNotEqual(0, failed.returncode)
        self.assertNotIn('refs/heads/rejected', self.refs())

    def test_process_exit_at_native_archive_and_response_boundaries(self):
        def stop(name):
            if name == 'staged':
                raise OSError('save a real native request without applying it')
        self.coordinator.boundary = stop
        with endpoint(self.coordinator) as url:
            self.assertNotEqual(0, self.push(url, 'accepted').returncode)
        request = self.coordinator.last_request
        script = """
import os, sys
from pathlib import Path
import gitpickle_archive as archive
from gitpickle_publish_spike import Coordinator
store, work, fault = sys.argv[1:]
def boundary(name):
    if name == fault:
        os._exit(73)
archive.boundary = boundary
coordinator = Coordinator(store, work, boundary=boundary)
coordinator.receive(sys.stdin.buffer.read(), lambda data: os.write(1, data))
raise SystemExit('fault was not reached')
"""
        faults = ['staged', 'native_complete', 'before_payload', 'after_payload',
                  'before_manifest', 'after_manifest', 'before_publication',
                  'after_publication', 'before_reply', 'before_final', 'after_final']
        for fault in faults:
            with self.subTest(fault=fault):
                store = self.root / ('store-' + fault)
                shutil.copytree(self.store, store)
                work = self.root / ('work-' + fault)
                env = dict(native.environment(), PYTHONPATH=str(Path(__file__).resolve().parent))
                result = subprocess.run([sys.executable, '-B', '-c', script, str(store), str(work), fault],
                                        input=request, capture_output=True, env=env, timeout=30)
                self.assertEqual(73, result.returncode, result.stderr)
                committed = fault in ['after_publication', 'before_reply', 'before_final', 'after_final']
                coordinator = probe.Coordinator(store, work)
                manifest = archive.read(store, coordinator.point(), 'point')
                self.assertEqual(committed, 'refs/heads/accepted' in manifest['refs'])
                if fault == 'after_final':
                    self.assertIn(b'unpack ok', probe.gate(io.BytesIO(result.stdout), lambda data: None))
                else:
                    with self.assertRaises(ValueError):
                        probe.gate(io.BytesIO(result.stdout), lambda data: None)
                shutil.rmtree(work)
                coordinator = probe.Coordinator(store, work)
                with endpoint(coordinator) as url:
                    retry = self.push(url, 'accepted')
                    self.assertEqual(0, retry.returncode, retry.stderr)
                    if committed:
                        self.assertIn(b'[up to date]', retry.stdout)
                archive.restore(store, coordinator.point(), self.root / ('restored-' + fault))

    def test_response_frames_fragmentation_and_non_sideband(self):
        payload = probe.packet(b'unpack ok\n') + probe.packet(b'ok refs/heads/main\n') + b'0000'
        class Fragments(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 1))
        self.assertEqual(payload, probe.gate(Fragments(payload), self.fail))
        progress, status = probe.packet(b'\x02progress\n'), probe.packet(b'\x01' + payload)
        sent = []
        self.assertEqual(status + b'0000', probe.gate(io.BytesIO(progress + status + b'0000'), sent.append))
        self.assertEqual([progress], sent)
        for invalid in [b'zzzz', b'0007x', b'0003', b'0000x', probe.packet(b'\x03fatal'), payload[:-4], b'']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                probe.gate(io.BytesIO(invalid), sent.append)
        with self.assertRaises(ValueError):
            probe.gate(io.BytesIO(payload), sent.append, limit=1)


if __name__ == '__main__':
    unittest.main()
