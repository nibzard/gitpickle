"""Status acceptance uses actual captures and archive-only recovery evidence."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_status as s
from gitpickle_writer import Writer


class StatusTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated/status')
        base.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'status') + '-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'status-test')
        self.repo = self.root / 'source.git'
        self.repo.mkdir()
        a.git(self.repo, 'init', '--bare', '--template=', '--initial-branch=main')
        self.clock = datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()
        timestamp = patch.object(a, 'timestamp', lambda: datetime.fromtimestamp(
            self.clock, timezone.utc).isoformat().replace('+00:00', 'Z'))
        timestamp.start()
        self.addCleanup(timestamp.stop)

    def capture(self, repository='r', **kwargs):
        return a.capture(self.store, self.repo, repository, 'github:1', **kwargs)

    def status(self, **kwargs):
        return s.calculate(self.store, now=self.clock, **kwargs)

    def cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch.object(s.time, 'time', lambda: self.clock):
            code = s.main([*arguments, '--archive', str(self.store), '--json'])
        self.assertEqual('', err.getvalue())
        return code, json.loads(out.getvalue())

    def control(self, kind, payload):
        return a.record(self.store, 'control', {'kind': 'control', 'archive_id': 'status-test',
            'operation_id': 'test', 'record_type': kind, 'recorded_at': a.timestamp(),
            'predecessor': None, 'payload': dict(repository_id='r', **payload)})

    def registration(self, repos, error=None):
        a.record(self.store, 'protection/github', {'kind': 'protection', 'archive_id': 'status-test',
            'parent': None, 'value': {'repositories': repos, 'error': error}})

    def add_pointer(self):
        payload = b'required LFS content'
        oid = a.digest(payload)
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode()
        blob = a.git(self.repo, 'hash-object', '-w', '--stdin', data=pointer).decode().strip()
        tree = a.git(self.repo, 'mktree', data=f'100644 blob {blob}\tlarge\n'.encode()).decode().strip()
        commit = a.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, data=b'test').decode().strip()
        a.git(self.repo, 'update-ref', 'refs/heads/main', commit)
        return oid, payload

    def test_freshness_boundary_and_successful_degraded_queries(self):
        self.capture()
        self.assertTrue(self.status()['data']['healthy'])
        self.clock += 8 * 3600
        self.assertTrue(self.status()['data']['healthy'])
        self.clock += 1
        self.assertEqual((0, 'ok'), (self.cli('status')[0], self.cli('status')[1]['outcome']))
        self.assertEqual(6, self.cli('status', '--check')[0])
        self.assertEqual(6, self.cli('doctor', '--attention')[0])
        result = self.status()
        self.assertEqual({'stale_coverage'}, {g['code'] for g in result['coverage_gaps']})
        self.assertTrue(self.status(freshness=9 * 3600)['data']['healthy'])
        self.assertIsNotNone(result['data']['repositories'][0]['last_verified_point'])

    def test_unchanged_capture_refreshes_source_without_content_change_or_payload(self):
        first = self.capture()
        before = self.status()['data']['repositories'][0]['timestamps']
        self.clock += 9 * 3600
        original = a.put
        def no_payload(root, key, data):
            self.assertNotIn('/packs/', key)
            self.assertNotIn('/lfs/', key)
            original(root, key, data)
        with patch.object(a, 'put', no_payload):
            self.assertEqual(first, self.capture())
        result = self.status()
        after = result['data']['repositories'][0]['timestamps']
        self.assertTrue(result['data']['healthy'])
        self.assertEqual(before['content_change'], after['content_change'])
        for name in ('source_observation', 'capture_completion', 'verification'):
            self.assertNotEqual(before[name]['at'], after[name]['at'])
            self.assertEqual(first, after[name]['point'])
            self.assertEqual(['git', 'lfs'], after[name]['components'])
        self.assertIsNone(after['restore_test'])

    def test_capture_replay_does_not_refresh_source(self):
        self.capture(operation_id='same')
        self.clock += 9 * 3600
        self.capture(operation_id='same')
        self.assertFalse(self.status()['data']['healthy'])

    def test_missing_lfs_then_repair_does_not_invent_content_change(self):
        oid, payload = self.add_pointer()
        partial = self.capture()
        before = self.status()['data']['repositories'][0]['timestamps']['content_change']
        result = self.status()
        coverage = result['data']['repositories'][0]['coverage']
        self.assertTrue(coverage['git']['complete'])
        self.assertFalse(coverage['lfs']['complete'])
        self.assertIn({'repository_id': 'r', 'component': 'lfs', 'code': 'incomplete_coverage'}, result['coverage_gaps'])
        self.assertEqual(6, self.cli('status', '--check')[0])
        self.clock += 60
        lfs = self.root / 'lfs'
        lfs.mkdir()
        (lfs / oid).write_bytes(payload)
        repaired = self.capture(lfs=lfs)
        self.assertNotEqual(partial, repaired)
        result = self.status()
        self.assertTrue(result['data']['healthy'])
        self.assertEqual(before, result['data']['repositories'][0]['timestamps']['content_change'])

    def test_required_wiki_and_new_policy_requirement(self):
        self.capture()
        self.registration({'1': {'repository_id': 'r', 'protected': True, 'available': True, 'has_wiki': True}})
        result = self.status()
        self.assertFalse(result['data']['healthy'])
        self.assertIn('wiki', {g['component'] for g in result['coverage_gaps']})
        child = self.capture('r-wiki')
        self.capture(wiki=child)
        result = self.status()
        self.assertTrue(result['data']['healthy'])
        self.assertEqual(['r'], result['scope']['repository_ids'])

    def test_sampled_restore_is_limited_to_tested_repository_and_point(self):
        point = self.capture()
        self.capture('other')
        shutil.rmtree(self.repo)
        shutil.rmtree(self.store / '.projection')
        with Writer(self.store) as writer:
            evidence = s.restore_test(writer, 'r', point)
        result = self.status()
        self.assertEqual(['r'], result['data']['restore_tested_repository_ids'])
        rows = {r['repository_id']: r for r in result['data']['repositories']}
        self.assertEqual(point, rows['r']['timestamps']['restore_test']['point'])
        self.assertEqual(evidence, rows['r']['timestamps']['restore_test']['evidence_id'])
        self.assertIsNone(rows['other']['timestamps']['restore_test'])
        self.assertEqual([], rows['other']['restore_tests'])
        self.assertEqual(2, result['data']['current_and_verified'])

    def test_source_loss_preserves_recovery_point_and_reports_next_action(self):
        point = self.capture()
        self.clock += 1
        self.control('source_failure', {'code': 'authentication'})
        code, result = self.cli('status', '--check')
        self.assertEqual(6, code)
        self.assertIn('authentication', {g['code'] for g in result['coverage_gaps']})
        self.assertEqual(point, result['data']['repositories'][0]['last_verified_point'])
        self.assertEqual('reconnect_source', result['next_action']['operation'])
        self.clock += 1
        self.capture()
        self.assertTrue(self.status()['data']['healthy'])

    def test_discovery_does_not_refresh_content_and_portfolio_pending_scope(self):
        self.capture()
        self.clock += 9 * 3600
        repos = {str(i): {'repository_id': 'r' if i == 1 else 'github-' + str(i),
                         'protected': True, 'available': True, 'observed_at': self.clock} for i in range(1, 405)}
        self.registration(repos)
        result = self.status()
        self.assertEqual(404, len(result['scope']['repository_ids']))
        self.assertEqual(404, result['data']['need_attention'])
        self.assertEqual(1, len(self.status(repository='r')['scope']['repository_ids']))
        self.assertEqual(s.EXCLUDED, result['data']['not_included'])
        self.assertTrue(all(g['component'] not in s.EXCLUDED for g in result['coverage_gaps']))

    def test_new_source_tracking_mismatch_and_git_only_freshness(self):
        self.capture()
        self.clock += 9 * 3600
        self.control('source_tracking', {'refs': '', 'head': {'symbolic': 'refs/heads/main'}, 'components': ['git']})
        row = self.status()['data']['repositories'][0]
        self.assertTrue(row['coverage']['git']['fresh'])
        self.assertFalse(row['coverage']['lfs']['fresh'])
        self.control('source_tracking', {'refs': 'refs/heads/new ' + 'a' * 40 + '\n', 'components': ['git']})
        self.assertFalse(self.status()['data']['healthy'])

    def test_prior_point_evidence_cannot_verify_latest_state(self):
        point = self.capture()
        self.add_pointer()
        self.clock += 1
        new = self.capture()
        for key, value in list(s.records(self.store, 'repos/r/checks', 'evidence')):
            if value['point'] == new:
                (self.store / key).unlink()
        result = self.status()
        self.assertFalse(result['data']['healthy'])
        self.assertEqual(point, result['data']['repositories'][0]['last_verified_point'])
        self.assertIn('unverified_coverage', {g['code'] for g in result['coverage_gaps']})

    def test_empty_and_future_evidence_are_not_healthy(self):
        self.assertFalse(self.status()['data']['healthy'])
        self.capture()
        self.clock -= 1
        self.assertFalse(self.status()['data']['healthy'])

    def test_equal_capture_times_follow_publication_order(self):
        self.capture()
        self.add_pointer()
        partial = self.capture()
        self.assertEqual(partial, self.status()['data']['repositories'][0]['point'])
        self.assertFalse(self.status()['data']['healthy'])
        # The first observation cannot hide the latest incomplete point.
        observation = self.status()['data']['repositories'][0]['coverage']['git']['source_observation']
        self.assertEqual(partial, observation['point'])

    def test_failed_restore_and_future_checks_do_not_grant_evidence(self):
        self.add_pointer()
        partial = self.capture()
        with Writer(self.store) as writer:
            s.restore_test(writer, 'r', partial)
        self.assertEqual([], self.status()['data']['restore_tested_repository_ids'])
        self.assertIsNone(self.status()['data']['repositories'][0]['timestamps']['restore_test'])
        for key, _ in list(s.records(self.store, 'repos/r/checks', 'evidence')):
            (self.store / key).unlink()
        self.clock += 60
        a.record(self.store, 'repos/r/checks', {'kind': 'evidence', 'archive_id': 'status-test',
            'repository_id': 'r', 'point': partial, 'recorded_at': a.timestamp(),
            'method': 'independent_restore', 'components': ['git', 'lfs'], 'passed': True, 'reader_version': 1})
        self.clock -= 60
        row = self.status()['data']['repositories'][0]
        self.assertIsNone(row['timestamps']['verification'])
        self.assertIsNone(row['timestamps']['restore_test'])

    def test_recognized_checkout_has_repository_scope(self):
        self.capture()
        self.capture('other')
        self.registration({'1': {'repository_id': 'r', 'protected': True, 'available': True,
                                'alias': 'owner/repo', 'aliases': []},
                           '2': {'repository_id': 'other', 'protected': True, 'available': True}})
        checkout = self.root / 'checkout'
        checkout.mkdir()
        a.git(checkout, 'init', '--template=')
        a.git(checkout, 'remote', 'add', 'origin', 'git@github.com:owner/repo.git')
        result = self.status(checkout=checkout)
        self.assertEqual(['r'], result['scope']['repository_ids'])
        self.assertEqual(1, result['data']['current_and_verified'])
        self.assertEqual(2, len(self.status()['scope']['repository_ids']))

    def test_actual_job_source_failure_survives_projection_loss(self):
        from gitpickle_jobs import Jobs, JobError
        class Unavailable:
            def execute(self, service, job):
                raise JobError('authentication')
        self.capture('github-1')
        self.clock += 1
        with Jobs(self.store, disk_reserve=0, source_interval=0, source_adapter=Unavailable()) as jobs:
            job = jobs.accept('actor', 'backup', 'failure', 'github-1', 'github', {'github_id': 1})
            jobs.tick()
            try:
                jobs.running[job['id']].result(timeout=5)
            except JobError:
                pass
            jobs.tick()
            self.assertEqual('failed', jobs.inspect(job['id'])['state'])
        shutil.rmtree(self.store / '.projection')
        result = self.status()
        self.assertFalse(result['data']['healthy'])
        self.assertIn('authentication', {g['code'] for g in result['coverage_gaps']})

    def test_query_is_read_only_and_reports_exclusions_in_text(self):
        self.capture()
        def contents():
            return {str(p): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        before = contents()
        for command in ('status', 'doctor'):
            self.assertEqual(0, self.cli(command)[0])
        self.assertEqual(before, contents())
        text = s.render(self.status())
        self.assertIn('GitHub metadata not included', text)
        self.assertIn('release_assets', text)
        self.assertIn('Z', text)

    def test_explicit_scope_does_not_read_unrelated_broken_point(self):
        self.capture()
        other = self.capture('other')
        (self.store / other).write_text('corrupt unrelated point')
        self.assertTrue(self.status(repository='r')['data']['healthy'])
        self.assertEqual('integrity_failure', self.cli('status')[1]['error']['code'])

    def test_conflicting_publications_have_a_distinct_query_error(self):
        self.capture()
        publication = a.read(self.store, a.inventory(self.store, 'r')[0], 'publication')
        a.record(self.store, 'repos/r/commits', dict(publication, operation_id='fork'))
        code, result = self.cli('status', '--check')
        self.assertEqual(4, code)
        self.assertEqual('failed', result['outcome'])
        self.assertEqual('conflict', result['error']['code'])

    def test_stable_query_failures_and_redaction(self):
        self.assertEqual(2, self.cli('status', '--freshness-seconds', '0')[0])
        self.assertEqual('invalid_input', self.cli('status', '--unknown')[1]['error']['code'])
        self.assertEqual('not_found', self.cli('status', '--repository', 'missing')[1]['error']['code'])
        with patch.object(s, 'calculate', side_effect=PermissionError('secret credential')):
            code, result = self.cli('status')
            self.assertEqual(3, code)
            self.assertEqual('forbidden', result['error']['code'])
            self.assertNotIn('secret credential', json.dumps(result))
        with patch.object(s, 'calculate', side_effect=a.ArchiveConflict('fork')):
            self.assertEqual((4, 'conflict'), (self.cli('status')[0], self.cli('status')[1]['error']['code']))
        with patch.object(s, 'calculate', side_effect=a.UnsupportedRecord('version')):
            self.assertEqual(7, self.cli('status')[0])
        with patch.object(s, 'calculate', side_effect=OSError('endpoint secret')):
            self.assertEqual(5, self.cli('status')[0])
        self.capture()
        (self.store / 'archive.json').write_text('corrupt')
        self.assertEqual('integrity_failure', self.cli('status')[1]['error']['code'])


if __name__ == '__main__':
    unittest.main()
