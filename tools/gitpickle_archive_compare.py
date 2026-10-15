"""Measure three representations on identical deterministic small fixtures."""
import json
import os
from pathlib import Path
import platform
import tempfile
import time

import gitpickle_archive as a
import gitpickle_perf as perf


def run():
    base = Path('fixtures/generated'); base.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(dir=base, prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'archive')+'-compare-') as temp:
        root = Path(temp).resolve()
        for fixture in ('empty-and-tiny', 'history-rewritten', 'lfs-2g'):
            source = root / fixture
            metadata = perf.generate(fixture, source, profile='smoke')
            for repo in sorted(source.glob('*.git')):
                original = a.git(repo, 'rev-parse', 'HEAD').decode().strip() if a.git(repo, 'for-each-ref') else None
                for strategy in ('full-bundle', 'reusable-pack', 'bounded-incremental'):
                    work = root / (fixture+'-'+repo.stem+'-'+strategy); work.mkdir()
                    store = work / 'archive'; a.initialize(store, 'comparison')
                    restored = work / 'restored'; restored.mkdir(); a.git(restored, 'init', '--bare')
                    total = 0; requests = 0; capture_seconds = 0; restore_seconds = 0
                    for generation in range(3):
                        if original:
                            if generation:
                                tree = a.git(repo, 'rev-parse', original+'^{tree}').decode().strip()
                                commit = perf.git(repo, 'commit-tree', tree, '-p', original, '-m', 'measured update '+str(generation)).decode().strip()
                                a.git(repo, 'update-ref', 'refs/heads/main', commit)
                            else:
                                a.git(repo, 'update-ref', 'refs/heads/main', original)
                        start = time.perf_counter()
                        if strategy == 'reusable-pack':
                            point = a.capture(store, repo, 'r', 'fixture:'+metadata['fixture_digest'], source / 'lfs')
                            capture_seconds += time.perf_counter()-start
                            start = time.perf_counter()
                            a.restore(store, point, work / ('point-'+str(generation)))
                            restore_seconds += time.perf_counter()-start
                        elif original:
                            bundle = work / (str(generation)+'.bundle')
                            args = ['bundle', 'create', str(bundle), '--all']
                            # Bound chain length to one incremental between checkpoints.
                            if strategy == 'bounded-incremental' and generation == 1:
                                args.append('^'+original)
                            a.git(repo, *args)
                            total += bundle.stat().st_size; requests += 1
                            capture_seconds += time.perf_counter()-start
                            start = time.perf_counter()
                            a.git(restored, 'fetch', str(bundle), '+refs/*:refs/*')
                            a.git(restored, 'fsck', '--full', '--strict')
                            if a.git(restored, 'for-each-ref') != a.git(repo, 'for-each-ref'):
                                raise ValueError('restored refs differ')
                            if strategy == 'bounded-incremental' and generation == 1:
                                missing = work / 'missing-base'; missing.mkdir()
                                a.git(missing, 'init', '--bare')
                                import subprocess
                                try:
                                    a.git(missing, 'bundle', 'verify', str(bundle))
                                except subprocess.CalledProcessError:
                                    pass
                                else:
                                    raise ValueError('incremental unexpectedly has no prerequisite')
                            restore_seconds += time.perf_counter()-start
                    if strategy == 'reusable-pack':
                        paths = list(store.glob('repos/r/packs/*'))
                        total = sum(p.stat().st_size for p in paths); requests = len(paths)
                    rows.append(dict(fixture=fixture, repository=repo.name, fixture_digest=metadata['fixture_digest'],
                        strategy=strategy, generations=3, git_payload_bytes=total, git_payload_writes=requests,
                        capture_seconds=capture_seconds, restore_seconds=restore_seconds, correctness='passed'))
    return dict(version=1, profile='smoke', git=a.git(Path('.'), '--version').decode().strip(),
                platform=platform.platform(), rows=rows)


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
