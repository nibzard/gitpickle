"""Local and CI validation with fail-closed evidence and paired revision runs."""
import argparse
import copy
from datetime import datetime, timezone
import html
import json
import math
import os
import signal
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

import gitpickle_perf as perf

ROOT = Path(__file__).resolve().parents[1]
FAULTS = {
    'archive-commit': ('test_gitpickle_capture', 'test_gitpickle_writer', 'test_gitpickle_components'),
    'job-replay': ('test_gitpickle_jobs',),
    'approval': ('test_gitpickle_plans', 'test_gitpickle_security'),
}
PENDING = {
    'full-size-performance': 'T051',
    's3-canary': 'T054',
    'b2-canary': 'T054',
    'fresh-vm-deployment': 'T055',
    'release-review-and-recovery': 'T056',
}


def save(path, value):
    perf.write_json(path, value)


def evidence_directory(path):
    path = Path(path).resolve()
    # Evidence must survive loss of the source checkout.
    if path == ROOT or ROOT in path.parents:
        raise ValueError('Evidence must be outside the source repository')
    path.mkdir(parents=True, exist_ok=True)
    out = path / (os.environ.get('LOOPER_RESOURCE_PREFIX', 'local').replace('/', '_') + '-' + str(time.time_ns()))
    out.mkdir(mode=0o700)
    return out


def run(command, cwd, log, timeout=1800, env=None):
    start = time.monotonic()
    with log.open('wb') as stream:
        try:
            child = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                     start_new_session=True, env=env)
            try:
                code = child.wait(timeout=timeout)
                status = 'passed' if code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                status, code = 'invalid', None
        except OSError as error:
            stream.write(str(error).encode())
            status, code = 'invalid', None
    return dict(command=command, status=status, exit_code=code,
                elapsed_seconds=time.monotonic()-start, log=str(log))


def fault_test(scenario, out):
    modules = [m for group in FAULTS.values() for m in group] if scenario == 'all' else FAULTS[scenario]
    command = [sys.executable, '-B', '-m', 'unittest', '-v', *modules]
    result = run(command, ROOT, out / 'fault.log',
                 env=dict(os.environ, PYTHONPATH=str(ROOT / 'tools')))
    # Preserve the exact model counterexamples and code mapping with every run.
    shutil.copytree(ROOT / 'spec/regressions', out / 'regressions')
    shutil.copy2(ROOT / 'spec/README.md', out / 'model-action-map.md')
    result['scenario'] = scenario
    save(out / 'fault.json', result)
    return result


def resolve_revision(value):
    return subprocess.check_output(['git', 'rev-parse', '--verify', value + '^{commit}'], cwd=ROOT, text=True).strip()


def export_revision(sha, destination):
    destination.mkdir()
    # Git archive creates a clean source tree without changing refs or worktree metadata.
    archive = destination.parent / (destination.name + '.tar')
    with archive.open('wb') as stream:
        subprocess.run(['git', 'archive', '--format=tar', sha], cwd=ROOT, stdout=stream, check=True)
    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter='data')
    archive.unlink()
    # The older benchmark harness asks Git for commit/dirty metadata.
    # A private repository with no shared refs supplies the exact archived revision.
    subprocess.run(['git', '-c', 'init.defaultBranch=benchmark', 'init', '--template=', '-q', str(destination)], check=True)
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(destination), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(destination), '-c', 'user.name=Gate', '-c',
                    'user.email=gate@example.invalid', '-c', 'core.hooksPath=/dev/null',
                    'commit', '-qm', 'Clean benchmark source'], check=True,
                   env=perf.git_env())


def combine(parts, sha):
    for part in parts:
        if sorted(part['expected_scenarios']) != sorted(parts[0]['expected_scenarios']):
            raise ValueError('Repetitions contain different scenarios')
        perf.compare(part, parts[0])
    result = copy.deepcopy(parts[0])
    result['commit'] = sha
    result['source_dirty'] = False
    result['repetitions'] = len(parts)
    for index, row in enumerate(result['rows']):
        row['raw_samples'] = [next(r for r in part['rows'] if r['scenario'] == row['scenario'])['raw_samples'][0] for part in parts]
        row['metrics'] = perf.summarize([s['elapsed_ms'] for s in row['raw_samples']])
    perf.validate_result(result)
    return result


def paired(args, out):
    candidate = resolve_revision(args.candidate)
    release = resolve_revision(args.release_baseline)
    if args.release_baseline != release:
        raise ValueError('The release baseline must be an immutable full commit identifier')
    merge = subprocess.check_output(['git', 'merge-base', candidate, args.merge_base], cwd=ROOT, text=True).strip()
    if args.repetitions < 2:
        raise ValueError('Paired runs require at least two repetitions')
    revisions = {'candidate': candidate, 'merge-base': merge, 'release': release}
    save(out / 'revisions.json', revisions)
    parts = {label: [] for label in revisions}
    generated = ROOT / '.cache/gates'
    generated.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'local') + '-paired-', dir=generated) as scratch:
        sources = {label: Path(scratch) / label for label in revisions}
        for label, sha in revisions.items():
            export_revision(sha, sources[label])
        for repetition in range(args.repetitions):
            # Reverse every other round to balance ordering and machine drift.
            order = list(revisions) if repetition % 2 == 0 else list(reversed(revisions))
            for label in order:
                output = out / f'{label}-{repetition}.json'
                command = [sys.executable, '-B', 'tools/gitpickle_perf.py', 'bench',
                           '--suite', args.bench_suite, '--profile', args.profile,
                           '--repetitions', '1', '--output', str(output)]
                if getattr(args, 'scenarios', None):
                    command.extend(['--scenarios', args.scenarios])
                result = run(command, sources[label], out / f'{label}-{repetition}.log')
                if result['status'] != 'passed':
                    raise ValueError(f'Invalid {label} repetition {repetition}; see {result["log"]}')
                part = json.loads(output.read_text())
                # Older baseline harnesses do not emit invocation identifiers.
                part['run_id'] = part.get('run_id', uuid.uuid4().hex)
                perf.validate_result(part)
                if part['source_dirty']:
                    raise ValueError('Benchmark changed tracked source')
                parts[label].append(part)
    results = {label: combine(value, revisions[label]) for label, value in parts.items()}
    for label, value in results.items():
        save(out / f'{label}.json', value)
    return results


def calibrate(baseline, repeat, pinned_runner):
    perf.compare(baseline, repeat)  # Validate compatibility and raw evidence first.
    if not baseline.get('run_id') or not repeat.get('run_id') or baseline['run_id'] == repeat['run_id']:
        raise ValueError('Noise characterization requires independent run identifiers')
    if baseline['commit'] != repeat['commit'] or baseline['source_dirty'] or repeat['source_dirty']:
        raise ValueError('Noise characterization requires the same clean baseline revision')
    floors = {}
    metrics = ('elapsed_ms', 'process_tree_peak_rss_bytes', 'staging_cache_peak_disk_bytes', 'cpu_seconds')
    for left in baseline['rows']:
        right = next(row for row in repeat['rows'] if row['scenario'] == left['scenario'])
        floors[left['scenario']] = {}
        for metric in metrics:
            values = [s[metric] for s in left['raw_samples'] + right['raw_samples']]
            spread = max(values) - min(values)
            minimum = {'elapsed_ms': 10, 'cpu_seconds': .01}.get(metric, 1024*1024)
            floors[left['scenario']][metric] = max(minimum, spread)
    return dict(schema_version=1, runner_fingerprint=baseline['runner_fingerprint'],
                pinned_runner=pinned_runner, baseline_commit=baseline['commit'],
                repetitions=min(baseline['repetitions'], repeat['repetitions']),
                fixture_digests={r['scenario']: r['fixture_digest'] for r in baseline['rows']},
                floors=floors, method='two independent baseline runs; maximum observed range')


def waiver_valid(waiver, scenario, baseline, candidate):
    required = ('owner', 'explanation', 'correctness_tradeoff_assessment', 'next_target', 'expires', 'scenario',
                'baseline_commit', 'candidate_commit')
    if any(not isinstance(waiver.get(k), str) or not waiver[k].strip() for k in required):
        return False
    try:
        expiry = datetime.fromisoformat(waiver['expires'].replace('Z', '+00:00'))
        return (expiry > datetime.now(timezone.utc) and waiver['scenario'] == scenario
                and waiver['baseline_commit'] == baseline['commit']
                and waiver['candidate_commit'] == candidate['commit'])
    except (ValueError, TypeError):
        return False


def evaluate(candidate, baseline, noise=None, confirmation=None, waivers=(), request_rationales=()):
    comparisons = perf.compare(candidate, baseline)
    # Correctness and evidence validation occur before any waiver is considered.
    if candidate['source_dirty'] or baseline['source_dirty']:
        raise ValueError('Regression gates require clean source')
    if confirmation is not None:
        perf.compare(confirmation, baseline)
        if not candidate.get('run_id') or not confirmation.get('run_id') or candidate['run_id'] == confirmation['run_id']:
            raise ValueError('Confirmation must be an independent candidate run')
        if confirmation['commit'] != candidate['commit'] or confirmation['source_dirty']:
            raise ValueError('Confirmation must use the same clean candidate')
    for comparison, row in zip(comparisons, candidate['rows']):
        old = next(r for r in baseline['rows'] if r['scenario'] == row['scenario'])
        scenario = row['scenario']
        characterized = (noise and noise.get('schema_version') == 1 and noise.get('pinned_runner')
                         and noise.get('runner_fingerprint') == candidate['runner_fingerprint']
                         and noise.get('repetitions', 0) >= 30
                         and noise.get('baseline_commit') == baseline['commit']
                         and noise.get('fixture_digests', {}).get(scenario) == row['fixture_digest'])
        signals = []
        for metric in ('elapsed_ms', 'process_tree_peak_rss_bytes', 'staging_cache_peak_disk_bytes', 'cpu_seconds'):
            before = [s[metric] for s in old['raw_samples']]
            after = [s[metric] for s in row['raw_samples']]
            previous, current = statistics.median(before), statistics.median(after)
            delta = current-previous
            if delta <= previous*.05:
                continue
            confirmed = False
            if characterized and confirmation and min(candidate['repetitions'], baseline['repetitions'], confirmation['repetitions']) >= 30:
                floor = noise['floors'][scenario][metric]
                if not isinstance(floor, (float, int)) or not math.isfinite(floor) or floor < 0:
                    raise ValueError('Invalid calibrated floor')
                again = next(r for r in confirmation['rows'] if r['scenario'] == scenario)
                baseline_upper = perf.summarize(before)['median_ci95_ms'][1]
                intervals = [perf.summarize(values)['median_ci95_ms'][0] for values in
                             (after, [s[metric] for s in again['raw_samples']])]
                confirmed = all(lower > baseline_upper*1.10 and lower-baseline_upper > floor for lower in intervals)
            signals.append(dict(metric=metric, delta=delta, confirmed=confirmed))
        requests = comparison['storage_request_delta']
        comparison['signals'] = signals
        comparison['baseline_ci95_ms'] = old['metrics']['median_ci95_ms']
        comparison['candidate_ci95_ms'] = row['metrics']['median_ci95_ms']
        comparison['noise_characterized'] = bool(characterized)
        comparison['gate'] = 'block' if any(s['confirmed'] for s in signals) else ('warn' if signals or any(requests.values()) else 'pass')
        if min(candidate['repetitions'], baseline['repetitions']) < 30 and comparison['gate'] == 'pass':
            comparison['gate'] = 'exploratory'
        if comparison['gate'] == 'block':
            accepted = next((w for w in waivers if waiver_valid(w, scenario, baseline, candidate)), None)
            if accepted:
                comparison['gate'] = 'waived'
                comparison['waiver'] = accepted
        growth = [op for op, delta in requests.items() if delta > 0
                  and min(candidate['repetitions'], baseline['repetitions']) >= 2
                  and all(len({s['storage_requests'][op] for s in r['raw_samples']}) == 1 for r in (row, old))]
        if growth:
            rationale = next((w for w in request_rationales if waiver_valid(w, scenario, baseline, candidate)), None)
            comparison['request_growth_requiring_review'] = growth
            if rationale:
                comparison['request_rationale'] = rationale
            elif comparison['gate'] != 'block':
                comparison['gate'] = 'review-required'
    return comparisons


def final_stages(args, out):
    """Execute operator-supplied acceptance commands; absent stages stay pending."""
    config = load(args.stage_config) or {}
    pending, records = {}, []
    required = dict(PENDING)
    if args.suite == 'nightly':
        required.pop('release-review-and-recovery')
    required['r2-canary'] = 'T022'
    if getattr(args, 'name', None):
        required = {args.name: required[args.name]}
    for name, task in required.items():
        stage = config.get(name)
        if not stage:
            pending[name] = task
            continue
        command = stage.get('command')
        if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
            raise ValueError('Stage commands must be argument arrays')
        # Full-size execution is restricted to the final allocated VM.
        if name == 'full-size-performance':
            profile = stage.get('profile', {})
            if (profile.get('host') != 'pve2' or not profile.get('exclusive')
                    or not all(profile.get(k) for k in ('hardware', 'network', 'allocation', 'limits'))):
                raise ValueError('Full-size runs require the pinned, exclusive pve2 VM profile')
            if shutil.disk_usage(ROOT).free < max(200 * 1024**3, stage.get('minimum_free_bytes', 0)):
                raise ValueError('Insufficient disk for full-size runs; require at least 200 GiB')
        environment = dict(os.environ, GITPICKLE_GATE_EVIDENCE=str(out),
                           GITPICKLE_GATE_STAGE=name)
        result = run(command, ROOT, out / (name + '.log'), timeout=args.timeout, env=environment)
        result['name'] = name
        # A zero exit code alone does not establish external acceptance.
        receipt = out / (name + '.json')
        evidence = load(receipt) if receipt.exists() else None
        expected = {
            'full-size-performance': ['large-cold-fixtures', 'dual-baselines', 'memory-disk-ceilings', 'request-amplification'],
            's3-canary': ['s3-conformance', 's3-recovery'],
            'b2-canary': ['b2-conformance', 'b2-recovery'],
            'fresh-vm-deployment': ['systemd', 'coolify', 'tailnet-https', 'bucket-only-recovery'],
            'release-review-and-recovery': ['security-review', 'model-review', 'protocol-review', 'git-compatibility', 'clean-machine-recovery'],
            'r2-canary': ['r2-conformance', 'r2-recovery'],
        }[name]
        if (not evidence or evidence.get('schema_version') != 1 or evidence.get('status') != 'passed'
                or evidence.get('commit') != resolve_revision('HEAD')
                or not all(evidence.get('checks', {}).get(k) == 'passed' for k in expected)
                or not evidence.get('raw_evidence')
                or not all((out / link).resolve().is_file() and (out / link).resolve().is_relative_to(out.resolve())
                           for link in evidence.get('raw_evidence', []))):
            result['status'] = 'invalid'
        records.append(result)
    return records, pending


def source_snapshot():
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
    values = {}
    for name in paths.split(b'\0'):
        if not name:
            continue
        path = ROOT / os.fsdecode(name)
        if path.is_symlink():
            values[os.fsdecode(name)] = 'link:' + os.readlink(path)
        elif path.is_file():
            values[os.fsdecode(name)] = [perf.digest(path), path.stat().st_mode & 0o777]
        else:
            values[os.fsdecode(name)] = 'missing'
    return values


def suite(args, out):
    before = source_snapshot()
    records = []
    commands = [('correctness', ['cargo', 'xtask', 'check']),
                ('models', ['cargo', 'xtask', 'model', '--suite', 'pr' if args.suite == 'pr' else 'nightly']),
                ('model-regressions', ['cargo', 'xtask', 'model', '--suite', 'regression']),
                ('liveness', ['cargo', 'xtask', 'model', '--suite', 'liveness']),
                ('small-benchmarks', [sys.executable, '-B', 'tools/gitpickle_perf.py', 'bench',
                                      '--suite', 'quick', '--profile', 'smoke', '--repetitions', '2',
                                      '--output', str(out / 'quick.json')]),
                ('application-benchmarks', [sys.executable, '-B', 'tools/gitpickle_capture_bench.py', '--repetitions', '2'])]
    for label, command in commands:
        record = run(command, ROOT, out / (label + '.log'), timeout=args.timeout)
        record['name'] = label
        records.append(record)
        save(out / 'suite.json', dict(schema_version=1, suite=args.suite, status='incomplete', checks=records))
    records.append(dict(name='faults', **fault_test('all', out)))
    # Copy all raw model output, including timeouts and counterexamples.
    if (ROOT / 'spec/results').exists():
        shutil.copytree(ROOT / 'spec/results', out / 'models')
    status = 'passed' if all(r['status'] == 'passed' for r in records) else 'failed'
    pending = {}
    if args.suite != 'pr':
        stages, pending = final_stages(args, out)
        records.extend(stages)
        if any(r['status'] != 'passed' for r in stages):
            status = 'failed'
    unchanged = source_snapshot() == before
    if not unchanged:
        status = 'failed'
    result = dict(commit=resolve_revision('HEAD'), source_unchanged=unchanged, schema_version=1, suite=args.suite, status='pending' if status == 'passed' and pending else status,
                  local_status=status, checks=records, pending=pending)
    save(out / 'suite.json', result)
    return 1 if status == 'failed' else (2 if pending else 0)


def comparison_report(result, out):
    groups = {'baseline': result} if isinstance(result, list) else result
    lines = ['<!doctype html><meta charset="utf-8"><title>Validation comparison</title>',
             '<h1>Validation comparison</h1>',
             '<p><a href="report.json">Raw decisions and uncertainty</a></p>',
             '<table><tr><th>Baseline</th><th>Scenario</th><th>Baseline ms</th>',
             '<th>Candidate ms</th><th>Delta %</th><th>95% intervals</th><th>Gate</th></tr>']
    for label, rows in groups.items():
        for row in rows:
            values = (label, row['scenario'], row['baseline_ms'], row['candidate_ms'],
                      row['delta_percent'], [row['baseline_ci95_ms'], row['candidate_ci95_ms']], row['gate'])
            lines.append('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in values) + '</tr>')
    (out / 'report.html').write_text('\n'.join(lines) + '</table>')


def aggregate(directory, suite_name):
    expected = set(PENDING) | {'r2-canary'}
    if suite_name == 'nightly':
        expected.remove('release-review-and-recovery')
    commit = resolve_revision('HEAD')
    local = [load(path) for path in Path(directory).rglob('suite.json')]
    if len(local) != 1 or local[0].get('local_status') != 'passed' or not local[0].get('source_unchanged'):
        raise ValueError('Missing or failed local acceptance evidence')
    if local[0].get('commit') != commit:
        raise ValueError('Local evidence refers to a different commit')
    completed = set()
    for path in Path(directory).rglob('stage.json'):
        result = load(path)
        if result.get('commit') != commit or result.get('pending'):
            raise ValueError('Incomplete or incompatible final stage')
        for check in result['checks']:
            if check['status'] != 'passed' or check['name'] in completed:
                raise ValueError('Failed or duplicate final stage')
            completed.add(check['name'])
    missing = sorted(expected - completed)
    return dict(schema_version=1, commit=commit, suite=suite_name,
                status='pending' if missing else 'passed', missing=missing,
                completed=sorted(completed))


def load(path):
    return json.loads(Path(path).read_text()) if path else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    combined = sub.add_parser('aggregate')
    combined.add_argument('--input', type=Path, required=True)
    combined.add_argument('--suite', choices=('nightly', 'release'), required=True)
    stage = sub.add_parser('stage')
    stage.add_argument('--name', choices=(*PENDING, 'r2-canary'), required=True)
    stage.add_argument('--stage-config', type=Path, required=True)
    stage.add_argument('--suite', choices=('nightly', 'release'), default='release')
    stage.add_argument('--timeout', type=int, default=86400)
    fault = sub.add_parser('fault-test')
    fault.add_argument('--scenario', choices=(*FAULTS, 'all'), default='all')
    suites = sub.add_parser('validate')
    suites.add_argument('--suite', choices=('pr', 'nightly', 'release'), default='pr')
    suites.add_argument('--timeout', type=int, default=1800)
    suites.add_argument('--stage-config', type=Path)
    pair = sub.add_parser('compare-revisions')
    pair.add_argument('--candidate', default='HEAD')
    pair.add_argument('--merge-base', required=True)
    pair.add_argument('--release-baseline', required=True)
    pair.add_argument('--repetitions', type=int, default=30)
    pair.add_argument('--confirm', action='store_true')
    pair.add_argument('--pinned-runner')
    pair.add_argument('--bench-suite', choices=('quick', *perf.CATALOG, 'all'), default='quick')
    pair.add_argument('--profile', choices=('smoke', 'full'), default='smoke')
    pair.add_argument('--scenarios')
    calibration = sub.add_parser('calibrate')
    calibration.add_argument('--baseline', required=True)
    calibration.add_argument('--repeat', required=True)
    calibration.add_argument('--pinned-runner', required=True)
    gate = sub.add_parser('gate')
    gate.add_argument('--candidate', required=True)
    gate.add_argument('--baseline', required=True)
    gate.add_argument('--confirmation')
    gate.add_argument('--noise')
    gate.add_argument('--waivers')
    gate.add_argument('--request-rationales')
    for command in (combined, stage, fault, suites, pair, calibration, gate):
        command.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    out = evidence_directory(args.evidence)
    print(out, flush=True)
    try:
        if args.command == 'aggregate':
            result = aggregate(args.input, args.suite)
            save(out / 'acceptance.json', result)
            return 2 if result['missing'] else 0
        if args.command == 'stage':
            records, pending = final_stages(args, out)
            save(out / 'stage.json', dict(commit=resolve_revision('HEAD'), checks=records, pending=pending))
            return 2 if pending else int(any(r['status'] != 'passed' for r in records))
        if args.command == 'fault-test':
            return int(fault_test(args.scenario, out)['status'] != 'passed')
        if args.command == 'validate':
            return suite(args, out)
        if args.command == 'compare-revisions':
            results = paired(args, out)
            if args.confirm:
                if not args.pinned_runner or args.repetitions < 30:
                    raise ValueError('Confirmation requires a pinned runner and at least 30 repetitions')
                repeat_out = out / 'repeat'
                repeat_out.mkdir()
                repeated = paired(args, repeat_out)
                result = {}
                for label in ('merge-base', 'release'):
                    noise = calibrate(results[label], repeated[label], args.pinned_runner)
                    save(out / (label + '-noise.json'), noise)
                    result[label] = evaluate(results['candidate'], results[label], noise, repeated['candidate'])
            else:
                result = {label: evaluate(results['candidate'], results[label]) for label in ('merge-base', 'release')}
        elif args.command == 'calibrate':
            result = calibrate(load(args.baseline), load(args.repeat), args.pinned_runner)
        else:
            result = evaluate(load(args.candidate), load(args.baseline), load(args.noise),
                              load(args.confirmation), load(args.waivers) or (), load(args.request_rationales) or ())
        save(out / 'report.json', result)
        if args.command in ('gate', 'compare-revisions'):
            comparison_report(result, out)
        if args.command == 'compare-revisions':
            return int(any(row['gate'] in ('block', 'review-required') for rows in result.values() for row in rows))
        if args.command == 'gate':
            return int(any(row['gate'] in ('block', 'review-required') for row in result))
        return 0
    except Exception as error:
        save(out / 'invalid.json', dict(status='invalid', error=str(error)))
        print(f'Invalid validation: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
