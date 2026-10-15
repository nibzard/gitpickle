"""Pinned, bounded TLC execution. Generated evidence never changes specifications."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SHA = '936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88'
URL = 'https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def completed(code, output):
    return (code == 0 and 'Model checking completed. No error has been found.' in output
            and re.search(r'\d+ states generated, \d+ distinct states found', output) is not None)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--suite', choices=['pr', 'nightly', 'regression', 'liveness'], default='pr')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--jar', type=Path)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    jar = args.jar or ROOT / 'spec/tools/tla2tools-1.7.4.jar'
    if not jar.exists() and not args.jar:
        jar.parent.mkdir(parents=True, exist_ok=True)
        temporary = jar.with_suffix('.download')
        urllib.request.urlretrieve(URL, temporary)
        if digest(temporary) != SHA:
            temporary.unlink()
            raise ValueError('TLC download checksum mismatch')
        temporary.replace(jar)
    if digest(jar) != SHA:
        raise ValueError('TLC checksum mismatch')
    java = subprocess.run(['java', '-version'], capture_output=True, text=True)
    if java.returncode or not re.search(r'version "21\.', java.stderr):
        raise ValueError('Java 21 is required')
    prefix = re.sub(r'[^a-zA-Z0-9_.-]', '_', os.getenv('LOOPER_RESOURCE_PREFIX', 'local'))
    import tempfile
    parent = ROOT / 'spec/results'
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix=prefix + '-', dir=parent))
    records = []
    for model in (['JobReplay'] if args.suite == 'liveness' else ['ArchiveCommit', 'JobReplay', 'PlanApproval']):
        spec = ROOT / 'spec' / (model + '.tla')
        config = ROOT / 'spec/config' / f'{model}-{args.suite}.cfg'
        command = ['java', '-Xmx1g', '-cp', str(jar.resolve()), 'tlc2.TLC',
                   '-workers', '1', '-seed', '1', '-fp', '0', '-metadir', str(out / model),
                   '-config', str(config), str(spec)]
        start = time.monotonic()
        code = None
        try:
            run = subprocess.run(command, cwd=ROOT / 'spec', capture_output=True,
                                 text=True, timeout=args.timeout)
            output, code = run.stdout + run.stderr, run.returncode
            status = 'complete' if completed(code, output) else 'failed'
        except subprocess.TimeoutExpired as error:
            output = (error.stdout or b'').decode() + (error.stderr or b'').decode()
            status = 'incomplete'
        regression = args.suite == 'regression'
        expected = {'ArchiveCommit': 'CompletePointIsClosed', 'JobReplay': 'ReplayIsConsistent',
                    'PlanApproval': 'ApprovalMatchesExecution'}[model]
        if regression and code == 12 and f'Invariant {expected} is violated' in output:
            status = 'counterexample'
        log = out / (model + '.log')
        log.write_text(output)
        counts = re.search(r'([\d,]+) states generated, ([\d,]+) distinct states found', output)
        records.append(dict(model=model, status=status, exit_code=code,
                            specification_sha256=digest(spec), configuration_sha256=digest(config),
                            configuration=config.read_text(), runtime_seconds=time.monotonic()-start,
                            generated_states=int(counts[1].replace(',', '')) if counts else None,
                            distinct_states=int(counts[2].replace(',', '')) if counts else None,
                            command=command, log=str(log.relative_to(ROOT))))
    report = dict(schema_version=1, suite=args.suite, tlc_release='1.7.4',
                  tlc_version='2.19 of 08 August 2024', jar_sha256=SHA,
                  java=java.stderr, assumptions_sha256=digest(ROOT / 'spec/README.md'), runs=records)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(out / 'report.json')
    return 0 if all(r['status'] == ('counterexample' if args.suite == 'regression' else 'complete') for r in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
