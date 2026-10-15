"""Bounded local 404-repository TUI benchmark using a real daemon and PTY."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import pty
import select
import struct
import subprocess
import termios
import time

import gitpickle_archive as a
from gitpickle_plans import Grant, Policy
from gitpickle_tui import Browser
import test_gitpickle_workflow as fixture


def percentile(values):
    return sorted(values)[math.ceil(len(values) * .95) - 1]


def proc_usage(pid):
    values = {}
    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith(('VmRSS:', 'VmHWM:')):
            key, value, _ = line.split()
            values[key.rstrip(':')] = int(value)
    fields = Path(f'/proc/{pid}/stat').read_text().split()
    values['cpu_seconds'] = (int(fields[13]) + int(fields[14])) / os.sysconf('SC_CLK_TCK')
    return values


def terminal_sample(binary, url, token):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
    start = time.perf_counter()
    process = subprocess.Popen([str(binary), 'tui', '--server', url], stdin=slave, stdout=slave,
        stderr=slave, env=dict(os.environ, TERM='xterm', NO_COLOR='1', GITPICKLE_MANAGEMENT_TOKEN=token))
    os.close(slave)
    output = b''
    try:
        deadline = start + 30
        while b'quit' not in output:
            if time.perf_counter() > deadline:
                raise RuntimeError('No useful portfolio paint within 30 seconds: ' + repr(output[-300:]))
            if select.select([master], [], [], .1)[0]:
                output += os.read(master, 65536)
        first_paint = (time.perf_counter() - start) * 1000
        before = proc_usage(process.pid)
        time.sleep(.25)
        idle_cpu = proc_usage(process.pid)['cpu_seconds'] - before['cpu_seconds']
        navigation_cpu_start = proc_usage(process.pid)['cpu_seconds']
        os.write(master, b'j' * 100)
        refresh_bytes = 0
        end = time.perf_counter() + .25
        while time.perf_counter() < end:
            if select.select([master], [], [], .02)[0]:
                refresh_bytes += len(os.read(master, 65536))
        usage = proc_usage(process.pid)
        os.write(master, b'q')
        process.wait(timeout=5)
        if process.returncode != 0:
            raise RuntimeError('TUI failed')
        return dict(first_paint_ms=first_paint, first_frame_bytes=len(output),
            navigation_100_keys_bytes=refresh_bytes, navigation_cpu_seconds=usage['cpu_seconds'] - navigation_cpu_start,
            idle_sample_seconds=.25,
            idle_cpu_seconds=idle_cpu, **usage)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path('target/debug/gitpickle'))
    parser.add_argument('--output', type=Path, default=Path('perf/results/tui/portfolio-404.json'))
    args = parser.parse_args()
    test = fixture.WorkflowTests()
    test.setUp()
    try:
        ids = frozenset('github-' + str(i) for i in range(1, 405))
        test.policies.replace(Policy(2, tuple(Grant('owner', p, ids, 2**62)
            for p in ('status', 'backup', 'sync', 'plan_restore', 'apply_plan')), frozenset({'owner'}), str(test.root)))
        rows = {str(i): dict(repository_id='github-' + str(i), protected=True, available=True,
            observed_at=time.time(), aliases=['alice/project-' + str(i)]) for i in range(1, 405)}
        a.record(test.archive, 'protection/github', dict(kind='protection', archive_id='workflow-test',
            parent=None, value=dict(repositories=rows, error=None, policy=None, discovery_due=0)))
        browser = Browser(test.client)
        refresh_start = time.perf_counter()
        browser.refresh()
        refresh_ms = (time.perf_counter() - refresh_start) * 1000
        assert len(browser.rows) == 404
        searches, paints = [], []
        for i in range(1000):
            browser.query = str(i % 404 + 1)
            browser.mode = ('all', 'attention', 'verified')[i % 3]
            start = time.perf_counter()
            browser.select()
            searches.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            browser.content(79, 20)
            paints.append((time.perf_counter() - start) * 1000)
        samples = [terminal_sample(args.binary.resolve(), test.url, test.token) for _ in range(6)]
        report = dict(schema_version=1, fixture='404 registered repositories; no captured Git/LFS/wiki payload',
            platform=platform.platform(), cpu=platform.processor(), logical_cpus=os.cpu_count(),
            cpu_model=next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                            if line.startswith('model name')), 'unknown'),
            measured_at_unix_seconds=time.time(), python=platform.python_version(),
            binary=str(args.binary), profile='local debug binary; loopback daemon; 80x24 PTY; NO_COLOR',
            cache='First invocation and five warm invocations; OS caches are not evicted.',
            portfolio_refresh_ms=refresh_ms, search_filter_p95_ms=percentile(searches),
            paint_p95_ms=percentile(paints), first_invocation_ms=samples[0]['first_paint_ms'],
            warm_first_paint_p95_ms=percentile([s['first_paint_ms'] for s in samples[1:]]),
            samples=samples, limits='Client process RSS/CPU only. Excludes daemon. No reference-runner budget is asserted.')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        test.doCleanups()


if __name__ == '__main__':
    main()
