"""Regression checks for validation decisions and evidence isolation."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gitpickle_gates as gates
import gitpickle_perf as perf


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'local') + '-gates-')
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        manifest = perf.generate('empty-and-tiny', root / 'source', profile='smoke')
        (root / 'sample').mkdir()
        sample = perf.sample(root / 'source', manifest, 'no-change', root / 'sample')
        env, fingerprint = perf.environment()
        cls.result = dict(run_id='baseline', schema_version=1, record_kind='measured', status='complete', repetitions=30,
            expected_scenarios=['empty-and-tiny/no-change'], commit='a'*40, source_dirty=False,
            runner_fingerprint=fingerprint, environment=env, suite='quick', harness_sha256='0'*64,
            scope='native-git-local-storage-reference', comparison='not_run', rows=[dict(
                scenario='empty-and-tiny/no-change', fixture_digest=manifest['fixture_digest'],
                fixture_version=1, scenario_version=1, fixture_manifest=manifest, cache_state='warm',
                correctness='passed', raw_samples=[copy.deepcopy(sample) for _ in range(30)], metrics={})])
        cls.set_time(cls.result, 100)

    @staticmethod
    def set_time(result, value):
        for sample in result['rows'][0]['raw_samples']:
            sample['elapsed_ms'] = value
        result['rows'][0]['metrics'] = perf.summarize([value]*result['repetitions'])

    def test_warning_confirmation_and_noise_are_required(self):
        base = copy.deepcopy(self.result)
        candidate = copy.deepcopy(base)
        candidate['commit'] = 'b'*40
        candidate['run_id'] = 'candidate'
        self.set_time(candidate, 150)
        noise = gates.calibrate(base, dict(base, run_id='baseline-repeat'), 'pve2-profile-v1')
        self.assertEqual('warn', gates.evaluate(candidate, base)[0]['gate'])
        self.assertEqual('warn', gates.evaluate(candidate, base, noise)[0]['gate'])
        self.assertEqual('block', gates.evaluate(candidate, base, noise, dict(candidate, run_id='confirmation'))[0]['gate'])
        with self.assertRaises(ValueError):
            gates.evaluate(candidate, base, noise, candidate)
        with self.assertRaises(ValueError):
            gates.calibrate(base, base, 'pve2-profile-v1')
        self.assertEqual('warn', gates.evaluate(candidate, base, noise, dict(base, commit='b'*40, run_id='confirmation'))[0]['gate'])
        noise['runner_fingerprint'] = 'wrong'
        self.assertEqual('warn', gates.evaluate(candidate, base, noise, dict(candidate, run_id='confirmation'))[0]['gate'])

    def test_exact_waiver_and_correctness_cannot_be_waived(self):
        base = copy.deepcopy(self.result)
        candidate = copy.deepcopy(base)
        candidate['commit'] = 'b'*40
        candidate['run_id'] = 'candidate'
        self.set_time(candidate, 150)
        noise = gates.calibrate(base, dict(base, run_id='baseline-repeat'), 'pve2-profile-v1')
        waiver = dict(scenario=base['rows'][0]['scenario'], owner='maintainer', explanation='Reviewed added validation',
            correctness_tradeoff_assessment='No correctness loss', next_target='Reduce allocation overhead',
            expires='2099-01-01T00:00:00Z', baseline_commit=base['commit'], candidate_commit=candidate['commit'])
        self.assertEqual('waived', gates.evaluate(candidate, base, noise, dict(candidate, run_id='confirmation'), [waiver])[0]['gate'])
        for key, value in [('expires', '2000-01-01T00:00:00Z'), ('owner', ''), ('candidate_commit', 'other')]:
            bad = dict(waiver, **{key: value})
            self.assertEqual('block', gates.evaluate(candidate, base, noise, dict(candidate, run_id='confirmation'), [bad])[0]['gate'])
        for defect in ('dirty', 'correctness', 'missing', 'fixture', 'no-change', 'unauthorized'):
            broken = copy.deepcopy(candidate)
            if defect == 'dirty':
                broken['source_dirty'] = True
            elif defect == 'correctness':
                broken['rows'][0]['raw_samples'][0]['correctness']['lost_acknowledged_updates'] = 1
            elif defect == 'unauthorized':
                broken['rows'][0]['raw_samples'][0]['correctness']['unauthorized_writes'] = 1
            elif defect == 'missing':
                broken['rows'][0]['raw_samples'].pop()
            elif defect == 'fixture':
                broken['rows'][0]['fixture_digest'] = 'wrong'
            else:
                broken['rows'][0]['raw_samples'][0]['git_lfs_payload_uploaded_bytes'] = 1
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                gates.evaluate(broken, base, noise, dict(candidate, run_id='confirmation'), [waiver])

    def test_deterministic_request_growth_needs_rationale(self):
        base = copy.deepcopy(self.result)
        candidate = copy.deepcopy(base)
        candidate['commit'] = 'b'*40
        candidate['run_id'] = 'candidate'
        for sample in candidate['rows'][0]['raw_samples']:
            sample['storage_requests']['HEAD'] += 1
            sample['storage_events'].append(dict(operation='HEAD', uploaded_bytes=0, downloaded_bytes=0, retry=False))
        self.assertEqual('review-required', gates.evaluate(candidate, base)[0]['gate'])
        rationale = dict(scenario=base['rows'][0]['scenario'], owner='reviewer', explanation='Added integrity probe',
            correctness_tradeoff_assessment='Stronger checks', next_target='Keep the extra probe', expires='2099-01-01T00:00:00Z',
            baseline_commit=base['commit'], candidate_commit=candidate['commit'])
        self.assertEqual('warn', gates.evaluate(candidate, base, request_rationales=[rationale])[0]['gate'])

    def test_timeout_and_missing_tool_never_pass(self):
        root = Path(self.temp.name)
        result = gates.run(['definitely-missing-gitpickle-tool'], root, root / 'missing.log')
        self.assertEqual('invalid', result['status'])
        result = gates.run(['python3', '-c', 'import time; time.sleep(10)'], root, root / 'timeout.log', timeout=.01)
        self.assertEqual('invalid', result['status'])

    def test_evidence_cannot_be_inside_source(self):
        with self.assertRaises(ValueError):
            gates.evidence_directory(gates.ROOT / '.cache/evidence')

    def test_final_stages_stay_pending_without_resources(self):
        args = type('Args', (), dict(stage_config=None, suite='release'))()
        checks, pending = gates.final_stages(args, Path(self.temp.name))
        self.assertEqual([], checks)
        self.assertEqual('T051', pending['full-size-performance'])
        self.assertEqual('T054', pending['s3-canary'])
        self.assertEqual('T054', pending['b2-canary'])
        self.assertEqual('T055', pending['fresh-vm-deployment'])

    def test_stage_success_requires_raw_evidence_and_complete_receipt(self):
        root = Path(self.temp.name)
        config = root / 'stage-config.json'
        config.write_text(json.dumps({'s3-canary': {'command': ['canary']}}))
        args = type('Args', (), dict(stage_config=config, suite='release', name='s3-canary', timeout=1))()
        with patch.object(gates, 'run', side_effect=lambda *a, **kw: {'status': 'passed'}):
            records, pending = gates.final_stages(args, root)
            self.assertEqual('invalid', records[0]['status'])
            raw = root / 'wire.log'
            raw.write_text('retained wire evidence')
            receipt = dict(schema_version=1, status='passed', commit=gates.resolve_revision('HEAD'),
                checks={'s3-conformance': 'passed', 's3-recovery': 'passed'}, raw_evidence=['wire.log'])
            (root / 's3-canary.json').write_text(json.dumps(receipt))
            records, pending = gates.final_stages(args, root)
            self.assertEqual('passed', records[0]['status'])
            self.assertEqual({}, pending)

    def test_aggregate_requires_every_stage_for_same_commit(self):
        with tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'local') + '-aggregate-') as temp:
            root = Path(temp)
            commit = gates.resolve_revision('HEAD')
            (root / 'suite.json').write_text(json.dumps(dict(local_status='passed', source_unchanged=True, commit=commit)))
            result = gates.aggregate(root, 'release')
            self.assertEqual('pending', result['status'])
            for name in (*gates.PENDING, 'r2-canary'):
                directory = root / name
                directory.mkdir()
                (directory / 'stage.json').write_text(json.dumps(dict(commit=commit, pending={},
                    checks=[dict(name=name, status='passed')])))
            self.assertEqual('passed', gates.aggregate(root, 'release')['status'])
            (root / 'r2-canary/stage.json').write_text(json.dumps(dict(commit='old', pending={}, checks=[])))
            with self.assertRaises(ValueError):
                gates.aggregate(root, 'release')

    def test_combining_incompatible_repetitions_fails(self):
        base = copy.deepcopy(self.result)
        changed = copy.deepcopy(base)
        changed['runner_fingerprint'] = 'different'
        with self.assertRaises(ValueError):
            gates.combine([base, changed], base['commit'])

    def test_clean_exports_and_dual_baseline_order(self):
        root = Path(self.temp.name)
        args = type('Args', (), dict(candidate='HEAD', merge_base='HEAD', release_baseline=gates.resolve_revision('HEAD'),
            repetitions=2, bench_suite='quick', profile='smoke'))()
        calls = []
        def run(command, cwd, log):
            calls.append(cwd.name)
            value = copy.deepcopy(self.result)
            value['repetitions'] = 1
            value['rows'][0]['raw_samples'] = value['rows'][0]['raw_samples'][:1]
            value['rows'][0]['metrics'] = perf.summarize([100])
            Path(command[-1]).write_text(json.dumps(value))
            return dict(status='passed')
        with patch.object(gates, 'run', side_effect=run):
            output = gates.paired(args, root)
        self.assertEqual(['candidate', 'merge-base', 'release', 'release', 'merge-base', 'candidate'], calls)
        self.assertEqual({'candidate', 'merge-base', 'release'}, set(output))
        self.assertEqual(2, output['candidate']['repetitions'])


if __name__ == '__main__':
    unittest.main()
