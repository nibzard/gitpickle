"""Integration checks for deterministic fixtures and fail-closed evidence."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import gitpickle_perf as perf


class HarnessTests(unittest.TestCase):
    def setUp(self):
        root = perf.ROOT / 'fixtures/generated'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle')+'-test-', dir=root)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_deterministic_catalog_and_portfolio(self):
        for name in perf.CATALOG:
            with self.subTest(name=name):
                first = perf.generate(name, self.base / (name+'-a'), profile='smoke')
                second = perf.generate(name, self.base / (name+'-b'), profile='smoke')
                self.assertEqual(first, second)
                perf.validate_fixture(self.base / (name+'-a'))
                if name == 'portfolio-404':
                    self.assertEqual(404, len(first['repositories']))
                if name == 'lfs-2g':
                    self.assertEqual(8, len(first['lfs']))
                    self.assertEqual(8*4096, first['lfs_bytes'])
                if name == 'history-rewritten':
                    self.assertNotEqual(first['historical'][0]['refs'], first['repositories'][0]['refs'])

    def test_seed_changes_content_and_rejects_mutation(self):
        a = perf.generate('empty-and-tiny', self.base / 'a', seed=1)
        b = perf.generate('empty-and-tiny', self.base / 'b', seed=2)
        self.assertNotEqual(a['fixture_digest'], b['fixture_digest'])
        repo = self.base / 'a/tiny.git'
        perf.git(repo, 'update-ref', '-d', 'refs/heads/topic')
        with self.assertRaises(ValueError):
            perf.validate_fixture(self.base / 'a')

    def test_scenarios_restore_and_no_change(self):
        for name in ('empty-and-tiny', 'history-rewritten', 'lfs-2g'):
            source = self.base / name
            manifest = perf.generate(name, source, profile='smoke')
            for scenario in perf.SCENARIOS:
                with self.subTest(fixture=name, scenario=scenario):
                    work = self.base / (name+'-'+scenario)
                    work.mkdir()
                    result = perf.sample(source, manifest, scenario, work)
                    self.assertEqual('passed', result['correctness']['outcome'])
                    self.assertGreater(result['process_tree_peak_rss_bytes'], 0)
                    self.assertGreater(result['resource_samples'], 0)
                    if scenario == 'no-change':
                        self.assertEqual(0, result['git_lfs_payload_uploaded_bytes'])
                    if scenario == 'verified-restore':
                        self.assertEqual(len(manifest['lfs']), result['correctness']['verified_lfs'])

    def test_failures_and_missing_dependency(self):
        for fault in perf.FAULTS:
            work = self.base / fault
            work.mkdir()
            result = perf.fault_sample(fault, work)
            self.assertEqual('passed', result['correctness']['outcome'])
        source = self.base / 'source'
        manifest = perf.generate('lfs-2g', source, profile='smoke')
        storage = perf.Storage(self.base / 'store')
        scratch = self.base / 'scratch'
        scratch.mkdir()
        perf.capture(source, manifest, storage, scratch, {})
        (storage.path / manifest['lfs'][0]['oid']).unlink()
        restore = self.base / 'restore'
        restore.mkdir()
        with self.assertRaises(FileNotFoundError):
            perf.restore(storage, restore, {})

    def test_reports_reject_incomplete_and_incompatible_evidence(self):
        source = self.base / 'source'
        manifest = perf.generate('empty-and-tiny', source, profile='smoke')
        work = self.base / 'work'
        work.mkdir()
        sample = perf.sample(source, manifest, 'no-change', work)
        row = dict(scenario='empty-and-tiny/no-change', fixture_digest=manifest['fixture_digest'],
                   fixture_version=1, scenario_version=1, fixture_manifest=manifest,
                   cache_state='warm', raw_samples=[sample], correctness='passed',
                   metrics=perf.summarize([sample['elapsed_ms']]))
        env, fingerprint = perf.environment()
        result = dict(schema_version=1, record_kind='measured', status='complete', rows=[row],
                      repetitions=1, expected_scenarios=[row['scenario']], commit='0'*40,
                      runner_fingerprint=fingerprint, environment=env, suite='empty-and-tiny',
                      source_dirty=True, harness_sha256=perf.digest(Path(perf.__file__)),
                      scope='native-git-local-storage-reference', comparison='not_run')
        perf.validate_result(result)
        self.assertEqual('exploratory', perf.compare(result, result)[0]['gate'])
        self.assertIsNone(row['metrics']['elapsed_ms_p95'])
        for change in ('samples', 'correctness', 'version', 'no-change', 'nonfinite', 'incomplete', 'stages'):
            broken = copy.deepcopy(result)
            if change == 'samples':
                broken['rows'][0]['raw_samples'] = []
            elif change == 'correctness':
                broken['rows'][0]['raw_samples'][0]['correctness']['outcome'] = 'failed'
            elif change == 'version':
                broken['schema_version'] = 99
            elif change == 'no-change':
                broken['rows'][0]['raw_samples'][0]['git_lfs_payload_uploaded_bytes'] = 1
            elif change == 'nonfinite':
                broken['rows'][0]['raw_samples'][0]['elapsed_ms'] = float('nan')
            elif change == 'stages':
                broken['rows'][0]['raw_samples'][0]['stages_ms'] = {}
            else:
                broken['status'] = 'incomplete'
            with self.subTest(change=change), self.assertRaises(ValueError):
                perf.validate_result(broken)
        other = copy.deepcopy(result)
        other['rows'][0]['fixture_digest'] = 'different'
        with self.assertRaises(ValueError):
            perf.compare(result, other)
        with self.assertRaises(ValueError):
            perf.report(dict(result, status='invalid'), self.base / 'invalid.html')
        self.assertFalse((self.base / 'invalid.html').exists())
        missing_environment = copy.deepcopy(result)
        del missing_environment['environment']['git']
        with self.assertRaises(ValueError):
            perf.validate_result(missing_environment)
        baseline = copy.deepcopy(result)
        candidate = copy.deepcopy(result)
        for value, elapsed in ((baseline, 50), (candidate, 100)):
            value['repetitions'] = 30
            value['rows'][0]['raw_samples'] = [dict(copy.deepcopy(sample), elapsed_ms=elapsed) for _ in range(30)]
            value['rows'][0]['metrics'] = perf.summarize([elapsed]*30)
        self.assertEqual('block', perf.compare(candidate, baseline)[0]['gate'])
        self.assertEqual('pass', perf.compare(baseline, baseline)[0]['gate'])
        candidate = copy.deepcopy(baseline)
        for value, memory in ((baseline, 10*1024*1024), (candidate, 20*1024*1024)):
            for raw in value['rows'][0]['raw_samples']:
                raw['process_tree_peak_rss_bytes'] = memory
        self.assertEqual('block', perf.compare(candidate, baseline)[0]['gate'])

    def test_failed_setup_writes_invalid_result(self):
        args = type('Args', (), dict(repetitions=1, suite='quick', scenarios=None,
                                    output=str(self.base / 'invalid.json'), profile='smoke', baseline=None))()
        with mock.patch.object(perf, 'generate', side_effect=ValueError('setup failed')):
            with self.assertRaises(ValueError):
                perf.bench(args)
        result = json.loads((self.base / 'invalid.json').read_text())
        self.assertEqual('invalid', result['status'])
        self.assertFalse((self.base / 'invalid.html').exists())


if __name__ == '__main__':
    unittest.main()
