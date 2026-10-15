"""Small local capture benchmark. Output is evidence, not a provider performance claim."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import tempfile
import time
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_perf as fixtures


def measure(store, operation):
    metrics = {'put_requests': 0, 'get_requests': 0, 'uploaded_bytes': 0,
               'payload_bytes': 0, 'payload_requests': 0, 'stages_seconds': {}}
    put, read = a.put, a.read
    started = {}

    def write(root, key, data):
        if Path(root) == store:
            metrics['put_requests'] += 1
            metrics['uploaded_bytes'] += len(data)
            if '/packs/' in key or '/lfs/' in key:
                metrics['payload_bytes'] += len(data)
                metrics['payload_requests'] += 1
        return put(root, key, data)

    def fetch(root, key, kind=None):
        if Path(root) == store:
            metrics['get_requests'] += 1
        return read(root, key, kind)

    def boundary(name):
        if name.startswith('before_'):
            started[name[7:]] = time.perf_counter()
        elif name.startswith('after_') and name[6:] in started:
            stage = name[6:]
            metrics['stages_seconds'][stage] = time.perf_counter() - started[stage]

    start = time.perf_counter()
    with patch.object(a, 'put', write), patch.object(a, 'read', fetch), patch.object(a, 'boundary', boundary):
        result = operation()
    metrics['elapsed_seconds'] = time.perf_counter() - start
    return result, metrics


def benchmark(repetitions):
    samples = []
    base = Path('fixtures/generated').resolve()
    base.mkdir(parents=True, exist_ok=True)
    for repetition in range(repetitions):
        with tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'capture')+'-bench-') as temp:
            root = Path(temp)
            source = root / 'source'
            fixture = fixtures.generate('empty-and-tiny', source, profile='smoke')
            repo, store = source / 'tiny.git', root / 'archive'
            a.initialize(store, 'capture-benchmark')
            points = []
            sample = {'repetition': repetition, 'fixture_digest': fixture['fixture_digest'], 'scenarios': {}}
            for scenario in ('initial', 'no-change', 'incremental'):
                if scenario == 'incremental':
                    parent = a.git(repo, 'rev-parse', 'HEAD').decode().strip()
                    tree = a.git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip()
                    commit = fixtures.git(repo, 'commit-tree', tree, '-p', parent, '-m', 'incremental').decode().strip()
                    a.git(repo, 'update-ref', 'refs/heads/main', commit)
                point, metrics = measure(store, lambda: a.capture(store, repo, 'r', 'fixture:tiny'))
                if scenario == 'no-change' and (point != points[0] or metrics['payload_bytes'] or metrics['payload_requests']):
                    raise ValueError('no-change capture uploaded payload or changed the point')
                points.append(point)
                sample['scenarios'][scenario] = metrics
            expected = a.git(repo, 'for-each-ref')
            shutil.rmtree(source)
            _, metrics = measure(store, lambda: a.restore(store, points[-1], root / 'restored.git'))
            if a.git(root / 'restored.git', 'for-each-ref') != expected:
                raise ValueError('archive-only restore changed refs')
            if len(a.inventory(store, 'r')) != 2:
                raise ValueError('capture created duplicate publications')
            sample['scenarios']['archive-only-restore'] = metrics
            sample['correctness'] = 'passed'
            samples.append(sample)
    return {'version': 1, 'scope': 'local filesystem; tiny fixture; no cloud provider claims',
            'environment': {'platform': platform.platform(), 'python': platform.python_version(),
                            'git': a.git(Path.cwd(), '--version').decode().strip()},
            'samples': samples,
            'median_seconds': {name: statistics.median(s['scenarios'][name]['elapsed_seconds'] for s in samples)
                               for name in samples[0]['scenarios']},
            'correctness': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=30)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('repetitions must be positive')
    print(json.dumps(benchmark(args.repetitions), indent=2, sort_keys=True))
