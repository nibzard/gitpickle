"""Deterministic discovery, authorization, identity, and protection acceptance tests."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import gitpickle_archive as a
from gitpickle_github import GitHub, Protection
from gitpickle_jobs import Jobs, JobError


def row(identity, name=None):
    return {'id': identity, 'full_name': name or 'owner/repo-' + str(identity), 'has_wiki': True}


class GitHubTests(unittest.TestCase):
    def setUp(self):
        addresses = patch('gitpickle_github.security.github_addresses', return_value=['1.1.1.1'])
        addresses.start()
        self.addCleanup(addresses.stop)
        base = Path('fixtures/generated/github')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'github') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'github-tests')
        self.now = 100.0
        self.rows = [row(1), row(2)]
        self.calls = []
        self.failure = None
        self.github = GitHub(lambda: 'private-token', transport=self.transport, clock=lambda: self.now)

    def transport(self, url, headers):
        self.calls.append((url, headers))
        if self.failure:
            return self.failure
        if '/repositories/' in url:
            identity = int(url.rsplit('/', 1)[1])
            value = next((r for r in self.rows if r['id'] == identity), None)
            return (200, {}, json.dumps(value).encode()) if value else (404, {}, b'')
        return 200, {}, json.dumps(self.rows).encode()

    def service(self):
        return Jobs(self.store, source_adapter=self.github, source_interval=0,
                    disk_reserve=0, clock=lambda: self.now)

    def protect(self, p, ids=None, future=False):
        preview = p.preview(ids, all_authorized=ids is None, include_future=future)
        return p.protect(preview, preview['confirmation'])

    def drain(self, jobs, ids):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            jobs.tick()
            if all(jobs.inspect(i)['state'] in ('completed', 'failed') for i in ids):
                return
            time.sleep(0.005)
        self.fail('jobs did not complete')

    def test_authenticated_pagination_of_404_authorized_repositories(self):
        rows = [row(i) for i in range(1, 405)]
        for installation in (False, True):
            calls = []
            route = '/installation/repositories' if installation else '/user/repos'
            def transport(url, headers):
                calls.append(url)
                self.assertEqual('Bearer private-token', headers['Authorization'])
                page = int(url.rsplit('=', 1)[1])
                batch = rows[(page - 1) * 100:page * 100]
                links = {'Link': f'<https://api.github.com{route}?per_page=100&page={page+1}>; rel="next"'} if page < 5 else {}
                return 200, links, json.dumps({'repositories': batch} if installation else batch).encode()
            client = GitHub(lambda: 'private-token', installation=installation, transport=transport)
            self.assertEqual(404, len(client.repositories()))
            self.assertEqual(5, len(calls))

    def test_pagination_never_sends_credentials_to_other_hosts_or_routes(self):
        for link in ('https://evil.invalid/user/repos?page=2',
                     'https://api.github.com/other?page=2',
                     'http://api.github.com/user/repos?page=2',
                     'https://api.github.com/user/repos?per_page=100&page=1'):
            calls = []
            def transport(url, headers):
                calls.append(url)
                return 200, {'Link': f'<{link}>; rel="next"'}, b'[]'
            with self.assertRaises(JobError):
                GitHub(lambda: 'secret', transport=transport).repositories()
            self.assertEqual(1, len(calls))

    def test_partial_authorization_scope_and_confirmed_future_inclusion(self):
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            with self.assertRaisesRegex(JobError, 'explicit_scope_required'):
                p.preview()
            with self.assertRaisesRegex(JobError, 'source_unavailable'):
                p.preview([99])
            preview = p.preview([1])
            with self.assertRaisesRegex(JobError, 'confirmation_required'):
                p.protect(preview, 'no')
            self.assertIsNone(p.state['policy'])
            self.protect(p, [1])
            self.rows.append(row(3))
            p.sync()
            self.assertEqual({'1'}, set(p.state['repositories']))
            with self.assertRaisesRegex(JobError, 'explicit_scope_required'):
                p.preview([1], include_future=True)
            self.protect(p, future=True)
            self.rows.append(row(4))
            p.sync()
            self.assertEqual(4, len(p.state['repositories']))
            self.assertTrue(p.state['policy']['include_future'])
            self.rows = [r for r in self.rows if r['id'] != 4]
            p.sync()
            self.assertTrue(p.state['repositories']['4']['protected'])
            self.assertFalse(p.state['repositories']['4']['available'])
            self.assertEqual([], list((self.store / 'repos').glob('*/commits/*')))

    def test_stale_confirmation_does_not_apply_and_rename_transfer_keep_identity(self):
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            preview = p.preview([1])
            self.rows[0] = row(1, 'new-owner/renamed')
            with self.assertRaisesRegex(JobError, 'plan_stale'):
                p.protect(preview, preview['confirmation'])
            self.protect(p, [1])
            self.rows[0] = row(1, 'third-owner/transfer')
            p.sync()
            repo = p.state['repositories']['1']
            self.assertEqual('github-1', repo['repository_id'])
            self.assertEqual(['new-owner/renamed', 'third-owner/transfer'], repo['aliases'])
            self.rows = [row(99, 'third-owner/transfer')]
            p.sync()
            self.assertFalse(p.state['repositories']['1']['available'])
            self.assertNotIn('99', p.state['repositories'])

    def test_page_failure_is_atomic_rate_limit_and_credential_loss_survive_restart(self):
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            self.protect(p, future=True)
            original = self.github.transport
            def incomplete(url, headers):
                if 'page=2' in url:
                    return 429, {'Retry-After': '120'}, b'secret diagnostics'
                return 200, {'Link': '<https://api.github.com/user/repos?page=2>; rel="next"'}, json.dumps([row(3)]).encode()
            self.github.transport = incomplete
            with self.assertRaisesRegex(JobError, 'rate_limited'):
                p.sync()
            self.assertEqual(220, p.state['discovery_due'])
            self.assertEqual({'1', '2'}, set(p.state['repositories']))
            self.assertTrue(p.state['repositories']['1']['available'])
            self.assertEqual([], p.tick(staging_bytes=1))
            self.github.transport = original
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            self.assertEqual(220, p.state['discovery_due'])
            self.failure = (401, {}, b'private-token')
            with self.assertRaisesRegex(JobError, 'authentication'):
                p.sync()
            self.assertFalse(p.state['repositories']['1']['available'])
            self.assertEqual([], p.tick(staging_bytes=1))
            self.failure = None
            p.sync()
            self.assertTrue(p.state['repositories']['1']['available'])
        for path in self.store.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'private-token', path.read_bytes())

    def test_hosted_mode_pauses_scheduling_and_already_queued_source_jobs(self):
        from gitpickle_hosted import change, head
        from gitpickle_hosting import Hosting
        from gitpickle_plans import Grant, Plans, Policy, PolicyStore, Principal
        with self.service() as jobs:
            protection = Protection(jobs, self.github)
            self.protect(protection, [1])
            queued = jobs.accept('protection', 'backup', 'queued-before-conversion', 'github-1',
                                 'github', {'github_id': 1}, staging_bytes=100)
            policies = PolicyStore(Policy(1, tuple(Grant('owner', permission, frozenset({'github-1'}), 2**62)
                                   for permission in ('git_read', 'hosting_manage')), frozenset(), str(self.store)))
            with Plans(self.store, policies, writer=jobs.writer) as plans:
                hosting = Hosting(plans, 'https://example.invalid/git')
                principal = Principal('owner')
                change(hosting, principal, 'github-1', 'create', None)
                self.assertEqual([], protection.tick(staging_bytes=100))
                self.drain(jobs, [queued['id']])
                self.assertEqual('source_updates_paused', jobs.inspect(queued['id'])['error'])
                with self.assertRaisesRegex(JobError, 'source_updates_paused'):
                    protection.backup([1], key='manual', staging_bytes=100)
                change(hosting, principal, 'github-1', 'resume', head(self.store, 'github-1'))
                self.assertEqual(1, len(protection.tick(staging_bytes=100)))

    def test_six_hour_capture_daily_discovery_and_restart_deduplication(self):
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            self.protect(p, [1])
            first = p.tick(staging_bytes=100)
            self.assertEqual(1, len(first))
            self.assertEqual([], p.tick(staging_bytes=100))
            self.assertEqual(21700, p.state['repositories']['1']['capture_due'])
            self.assertEqual(86500, p.state['discovery_due'])
            # Lose the policy checkpoint after durable job acceptance.
            head = p.head
        (self.store / head).unlink()
        shutil.rmtree(self.store / '.projection')
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            repeated = p.tick(staging_bytes=100)
            self.assertEqual(first[0]['id'], repeated[0]['id'])
            self.assertEqual(1, len(jobs.jobs))
            self.now += 6 * 3600
            self.assertEqual(1, len(p.tick(staging_bytes=100)))
            self.assertEqual(2, len(jobs.jobs))
            self.now = 86500
            self.rows.append(row(3))
            p.tick(staging_bytes=100)
            self.assertEqual(172900, p.state['discovery_due'])
            self.assertNotIn('3', p.state['repositories'])

    def test_custom_intervals_require_confirmation_and_positive_seconds(self):
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            with self.assertRaisesRegex(JobError, 'invalid_input'):
                p.preview([1], capture_interval=0)
            preview = p.preview([1], capture_interval=60, discovery_interval=120)
            p.protect(preview, preview['confirmation'])
            p.tick(staging_bytes=1)
            self.assertEqual(160, p.state['repositories']['1']['capture_due'])
            self.assertEqual(220, p.state['discovery_due'])

    def test_portfolio_schedule_is_explicit_and_bounded_by_jobs(self):
        self.rows = [row(i) for i in range(1, 405)]
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            self.protect(p)
            captures = p.tick(staging_bytes=10)
            self.assertEqual(404, len(captures))
            with patch.object(jobs, '_execute', return_value='point'):
                # Wait at worker boundaries instead of imposing an aggregate
                # disk-speed deadline on hundreds of durable event writes.
                remaining = len(captures)
                for _ in range((len(captures) + jobs.limit - 1) // jobs.limit):
                    jobs.tick()
                    self.assertEqual(min(remaining, jobs.limit), len(jobs.running))
                    for future in tuple(jobs.running.values()):
                        self.assertEqual('point', future.result(timeout=10))
                    remaining -= len(jobs.running)
                jobs.tick()
                self.assertEqual({}, jobs.running)
                self.assertTrue(all(jobs.inspect(j['id'])['state'] == 'completed'
                                    for j in captures))
            self.assertEqual([], p.tick(staging_bytes=10))
        # Completion must survive loss of the scheduling projection.
        shutil.rmtree(self.store / '.projection')
        with self.service() as jobs:
            self.assertEqual(404, len(jobs.jobs))
            self.assertTrue(all(j['state'] == 'completed' for j in jobs.jobs.values()))
            self.assertEqual([], Protection(jobs, self.github).tick(staging_bytes=10))

    def test_backup_sync_disappearance_and_archive_only_recovery(self):
        bare = self.root / 'source.git'
        bare.mkdir()
        a.git(bare, 'init', '--bare', '--template=')
        tree = a.git(bare, 'mktree', data=b'').decode().strip()
        commit = a.git(bare, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, data=b'protected history\n').decode().strip()
        a.git(bare, 'update-ref', 'refs/heads/main', commit)
        a.git(bare, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        original_run = a.security.run
        def clone(command, **kwargs):
            self.assertTrue(any(url in command for url in (
                'https://github.com/owner/repo-1.git', 'https://github.com/owner/repo-1.wiki.git')))
            self.assertEqual('false', kwargs['env']['GIT_CONFIG_VALUE_1'])
            self.assertNotIn('private-token', str(command))
            return original_run(['git', 'clone', '--mirror', str(bare), command[-1]],
                                capture_output=True, check=True)
        with self.service() as jobs:
            # The shared runner is patched; keep real bounded archive Git calls.
            def dispatch(command, **kwargs):
                return clone(command, **kwargs) if any(str(arg).startswith('https://github.com/') for arg in command) else original_run(command, **kwargs)
            with patch('gitpickle_github.security.run', side_effect=dispatch):
                p = Protection(jobs, self.github)
                self.protect(p, [1])
                with self.assertRaisesRegex(JobError, 'explicit_scope_required'):
                    p.backup(key='now', staging_bytes=1)
                job = p.backup([1], key='now', staging_bytes=1)[0]
                self.drain(jobs, [job['id']])
                self.assertEqual('completed', jobs.inspect(job['id'])['state'])
                chain = a.inventory(self.store, 'github-1')
                self.assertEqual(1, len(chain))
                sync = jobs.accept('actor', 'sync', 's', 'github-1', 'github', {'github_id': 1})
                self.drain(jobs, [sync['id']])
                self.assertEqual('completed', jobs.inspect(sync['id'])['state'])
                self.assertEqual(chain, a.inventory(self.store, 'github-1'))
                self.rows = []
                p.sync()
                self.assertFalse(p.state['repositories']['1']['available'])
                self.assertTrue(p.state['repositories']['1']['protected'])
                self.assertEqual([], p.tick(staging_bytes=1))
                self.assertEqual(chain, a.inventory(self.store, 'github-1'))
        shutil.rmtree(bare)
        point = a.read(self.store, chain[-1], 'publication')['point']
        a.restore(self.store, point, self.root / 'recovered.git')
        a.git(self.root / 'recovered.git', 'fsck', '--full')
        self.assertEqual(commit, a.git(self.root / 'recovered.git', 'rev-parse', 'main').decode().strip())

    def test_lost_policy_response_reconciles_without_fork_and_missing_token_stops_access(self):
        with self.service() as jobs:
            p = Protection(jobs, self.github)
            preview = p.preview([1])
            original = a.put
            def lost(*args):
                original(*args)
                raise TimeoutError('lost response')
            with patch.object(a, 'put', side_effect=lost):
                p.protect(preview, preview['confirmation'])
            p = Protection(jobs, self.github)
            self.assertEqual({'1'}, set(p.state['repositories']))
            self.github.token = lambda: None
            before = len(self.calls)
            with self.assertRaisesRegex(JobError, 'authentication'):
                p.sync()
            self.assertEqual(before, len(self.calls))
            self.assertFalse(p.state['repositories']['1']['available'])

    def test_identity_change_during_clone_never_publishes(self):
        with self.service() as jobs:
            job = jobs.accept('actor', 'backup', 'rename', 'github-1', 'github',
                              {'github_id': 1}, staging_bytes=1)
            before = self.github.repository(row(1))
            after = self.github.repository(row(1, 'new-owner/renamed'))
            with patch.object(self.github, 'get', side_effect=[before, after]), \
                    patch('gitpickle_github.security.run') as clone, \
                    patch.object(jobs.writer, 'capture') as capture:
                with self.assertRaisesRegex(JobError, 'source_unavailable'):
                    self.github.execute(jobs, job)
                clone.assert_called_once()
                capture.assert_not_called()
            self.assertEqual([], list((self.store / '.projection' / 'github-staging').iterdir()))
            self.assertEqual([], a.inventory(self.store, 'github-1'))

    def test_unavailable_identity_stops_before_clone(self):
        with self.service() as jobs:
            job = jobs.accept('actor', 'backup', 'missing', 'github-1', 'github',
                              {'github_id': 1}, staging_bytes=1)
            self.rows = []
            with patch('gitpickle_github.security.run') as clone:
                with self.assertRaisesRegex(JobError, 'source_unavailable'):
                    self.github.execute(jobs, job)
                clone.assert_not_called()
            self.assertEqual([], a.inventory(self.store, 'github-1'))

    def test_status_codes_and_invalid_identity_fail_closed(self):
        for status, headers, code in [(403, {}, 'forbidden'), (404, {}, 'source_unavailable'),
                (403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '300'}, 'rate_limited')]:
            self.failure = status, headers, b''
            with self.assertRaisesRegex(JobError, code):
                self.github.repositories()
        self.failure = None
        self.rows = [row(0)]
        with self.assertRaisesRegex(JobError, 'invalid_response'):
            self.github.repositories()
        with patch.object(self.github, 'request', return_value=(row(2), {})):
            with self.assertRaisesRegex(JobError, 'identity_conflict'):
                self.github.get(1)


if __name__ == '__main__':
    unittest.main()
