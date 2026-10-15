"""Local integration probe, deliberately disconnected from writable hosting.

Native Git owns negotiation and ref results. Only outgoing packet framing is
inspected. Channel 2 is progress; all other output waits for archive publication.
The production caller must supply authorization and archive-wide ownership.
"""
import os
from pathlib import Path
import subprocess
import signal
import tempfile
import threading
import uuid

import gitpickle_archive as archive
import gitpickle_security as security


from gitpickle_receive import gate, packet


class Coordinator:
    """One repository, local archive contract, no product write capability."""
    def __init__(self, store, work, *, hooks=None, boundary=lambda name: None):
        self.store, self.work = Path(store), Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.hooks, self.boundary = hooks, boundary
        self.lock = threading.Lock()

    def point(self):
        chain = archive.inventory(self.store, 'r')
        return archive.read(self.store, chain[-1], 'publication')['point']

    def receive(self, request, send):
        # Serialize from committed base through publication and final response.
        with self.lock, tempfile.TemporaryDirectory(
                dir=self.work, prefix=os.getenv('LOOPER_RESOURCE_PREFIX', 'publish') + '-') as temp:
            stage = Path(temp) / 'stage.git'
            archive.restore(self.store, self.point(), stage)
            self.boundary('staged')
            # Only explicitly installed fixture hooks can run. Source hooks never enter staging.
            command = ['git', '-c', 'core.hooksPath=' + str(self.hooks or '/dev/null'),
                       '-c', 'gc.auto=0', '-c', 'receive.unpackLimit=1',
                       'receive-pack', '--stateless-rpc', str(stage)]
            with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as errors:
                source.write(request)
                source.seek(0)
                process = subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE,
                                           stderr=errors, env=security.git_environment(),
                                           start_new_session=True)
                def stop():
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                timer = threading.Timer(60, stop)
                timer.start()
                try:
                    final = gate(process.stdout, send)
                    if process.wait(timeout=5):
                        raise ValueError('native receive-pack failed')
                finally:
                    timer.cancel()
                    stop()
                    process.wait()
                    process.stdout.close()
            self.boundary('native_complete')
            # Git-only fixtures: reject pointers before any complete publication.
            if archive.required_lfs(stage):
                raise ValueError('LFS is outside this Git-only probe')
            self.boundary('before_publication')
            archive.capture(self.store, stage, 'r', 'hosted:probe', operation_id=str(uuid.uuid4()))
            self.boundary('after_publication')
            # Visibility is derived from the durable chain, not the disposable workspace.
            self.boundary('before_final')
            send(final)
            self.boundary('after_final')
