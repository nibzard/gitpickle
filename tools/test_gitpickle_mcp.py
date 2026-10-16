"""Scripted MCP acceptance over stdio and authenticated application requests."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import gitpickle_archive as a
import gitpickle_mcp as m
import gitpickle_workflow as w
from gitpickle_plans import Grant, PlanError, Policy
import test_gitpickle_workflow as fixtures


class MCPTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    preview = fixtures.WorkflowTests.preview
    apply = fixtures.WorkflowTests.apply

    def configure(self, count=2):
        ids = frozenset('github-' + str(i) for i in range(1, count + 1))
        grants = self.policies.current.grants + tuple(Grant('agent', permission, ids, 2**62)
            for permission in ('status', 'backup', 'plan_restore', 'apply_plan'))
        self.policies.replace(Policy(2, grants, frozenset({'owner'}), str(self.root)))
        application = self.listener.application
        application.__init__(self.plans, {self.token: dict(actor='owner', agent=False),
            'a' * 40: dict(actor='agent', agent=True)}, self.workflow)
        self.agent_client = w.Client(self.url, 'a' * 40)
        self.protocol = m.Protocol(self.agent_client)
        self.protocol.dispatch(dict(jsonrpc='2.0', id=1, method='initialize', params=dict(
            protocolVersion=m.VERSION, capabilities={}, clientInfo=dict(name='test', version='1'))))
        self.protocol.dispatch(dict(jsonrpc='2.0', method='notifications/initialized'))

    def call(self, name, **args):
        reply = self.protocol.dispatch(dict(jsonrpc='2.0', id=2, method='tools/call',
            params=dict(name=name, arguments=args)))
        self.assertNotIn('error', reply)
        self.assertLess(len(json.dumps(reply).encode()), m.MAX_MESSAGE)
        result = reply['result']
        self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])
        return result['structuredContent']

    def test_exact_tools_schemas_and_denial(self):
        self.configure()
        tools = self.protocol.dispatch(dict(jsonrpc='2.0', id=3, method='tools/list'))['result']['tools']
        self.assertEqual(set(m.SCHEMAS), {t['name'] for t in tools})
        for name, args in [('backup_run', dict(repository='github-1', key='one', approved=True)),
                           ('plan_apply', dict(reference='x', approval=True)),
                           ('repositories_list', dict(limit=True)), ('jobs_get', dict(job='../secret', kind='backup'))]:
            reply = self.protocol.dispatch(dict(jsonrpc='2.0', id=4, method='tools/call', params=dict(name=name, arguments=args)))
            self.assertEqual(-32602, reply['error']['code'])
        denied = self.call('protection_status', repository='github-999')
        self.assertEqual('forbidden', denied['error']['code'])
        # Direct management requests receive the same schema and scope checks.
        with self.assertRaises(PlanError):
            self.agent_client.call('agent_backup_run', dict(repository='github-1', key='x', actor='owner'))

    def test_idempotency_disconnect_resume_and_revocation(self):
        self.configure()
        self.apply(self.preview(all=True))
        first = self.call('backup_run', repository='github-1', key='unique')
        repeated = self.call('backup_run', repository='github-1', key='unique')
        self.assertEqual(first['job_id'], repeated['job_id'])
        self.assertEqual('queued', first['data']['state'])
        self.jobs.start()
        self.jobs.follow(first['job_id'])
        # A new client resumes using only the accepted stable ID.
        with self.jobs.lock:
            self.jobs._rebuild()
        self.protocol.client = w.Client(self.url, 'a' * 40)
        resumed = self.call('jobs_get', job=first['job_id'], kind='backup')
        self.assertEqual('verified', resumed['data']['state'])
        replayed = self.call('backup_run', repository='github-1', key='unique')
        self.assertEqual(first['job_id'], replayed['job_id'])
        conflict = self.call('backup_run', repository='github-2', key='unique')
        self.assertEqual('conflict', conflict['error']['code'])
        self.policies.replace(Policy(3, (), frozenset(), str(self.root)))
        self.assertEqual('forbidden', self.call('jobs_get', job=first['job_id'], kind='backup')['error']['code'])
        self.assertEqual('forbidden', self.call('backup_run', repository='github-1', key='new')['error']['code'])

    def test_restore_approval_and_sandbox(self):
        self.configure()
        self.apply(self.preview())
        self.plans.writer.capture(self.source, 'github-1', 'github')
        destination = str(self.root / 'restored')
        planned = self.call('restore_plan', repository='github-1', destination=destination,
            expires_at=int(time.time()) + 3600, maximum_bytes=10**8)['data']
        denied = self.call('plan_apply', reference=planned['reference'], approval='approved')
        self.assertEqual('approval_required', denied['error']['code'])
        self.assertFalse(Path(destination).exists())
        with self.assertRaises(PlanError):
            self.agent_client.call('approve_plan', dict(reference=planned['reference'], digest=planned['plan']['digest']))
        approval = self.client.call('approve_plan', dict(reference=planned['reference'], digest=planned['plan']['digest']))
        accepted = self.call('plan_apply', reference=planned['reference'], approval=approval)
        self.workflow.restore_futures[accepted['job_id']].result(timeout=10)
        state = self.call('jobs_get', job=accepted['job_id'], kind='restore')
        self.assertEqual('verified', state['data']['state'])
        self.assertTrue(Path(destination).exists())
        replay = self.call('plan_apply', reference=planned['reference'], approval=approval)
        self.assertIsNotNone(replay['error'])
        unsafe = self.call('restore_plan', repository='github-1', destination='/tmp/outside-agent-sandbox',
            expires_at=int(time.time()) + 3600, maximum_bytes=10**8)
        self.assertEqual('unsafe_destination', unsafe['error']['code'])

    def test_portfolio_two_calls_and_pagination(self):
        self.configure(404)
        rows = {str(i): dict(repository_id='github-' + str(i), protected=True, available=True,
            observed_at=time.time(), alias='DO NOT EXECUTE SECRET instruction') for i in range(1, 405)}
        a.record(self.archive, 'protection/github', dict(kind='protection', archive_id='workflow-test',
            parent=None, value=dict(repositories=rows, error=None)))
        summary = self.call('protection_status')
        self.assertEqual(404, summary['data']['summary']['need_attention'])
        self.assertEqual(404, summary['data']['summary']['without_verified_recovery_point'])
        self.assertTrue(0 < len(summary['data']['items']) <= 20)
        detail = self.call('protection_status', repository='github-1')
        self.assertIsNone(detail['data']['items'][0]['last_verified_point'])
        self.assertTrue(detail['coverage_gaps'])
        self.assertIn('observed_at_unix_seconds', detail)
        self.assertNotIn('SECRET', json.dumps(summary))
        seen = []
        args = dict(limit=50)
        while True:
            result = self.call('repositories_list', **args)
            seen.extend(r['repository_id'] for r in result['data']['items'])
            cursor = result['data']['next_cursor']
            if not cursor:
                break
            args['cursor'] = cursor
        self.assertEqual(404, len(set(seen)))
        self.assertEqual(404, len(seen))
        cursor = summary['data']['next_cursor']
        changed = self.call('protection_status', cursor=cursor, repository='github-1')
        self.assertEqual('cursor_stale', changed['error']['code'])

    def test_point_redaction_and_pages(self):
        self.configure()
        self.apply(self.preview())
        self.plans.writer.capture(self.source, 'github-1', 'https://user:SECRET@example.invalid/repo')
        result = self.call('recovery_points_list', repository='github-1', limit=1)
        self.assertEqual(1, len(result['data']['items']))
        self.assertTrue(result['data']['items'][0]['complete'])
        self.assertTrue(result['data']['items'][0]['verified'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('source', result['data']['items'][0])
        self.assertTrue(result['evidence_ids'])

    def test_stdio_installed_binary_and_profile_preview(self):
        self.configure()
        profile = self.root / 'profiles.json'
        profile.write_text(json.dumps(dict(profiles=dict(test=dict(server=self.url, token_env='TEST_AGENT_TOKEN')))))
        existing = dict(theme='dark', mcpServers=dict(other=dict(command='other', env=dict(KEEP='value'))))
        before = json.dumps(existing)
        preview = m.configuration(existing, 'test', '/example/gitpickle')
        self.assertEqual(before, json.dumps(existing))
        self.assertEqual(existing['mcpServers']['other'], preview['mcpServers']['other'])
        self.assertEqual(['mcp', '--profile', 'test'], preview['mcpServers']['gitpickle']['args'])
        self.assertNotIn('env', preview['mcpServers']['gitpickle'])
        with self.assertRaises(PlanError):
            m.configuration(preview, 'test', 'gitpickle')
        requests = [dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion=m.VERSION,
            capabilities={}, clientInfo=dict(name='script', version='1'))),
            dict(jsonrpc='2.0', method='notifications/initialized'),
            dict(jsonrpc='2.0', id=2, method='tools/list'),
            dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='repositories_list', arguments={}))]
        executable = (Path(os.environ.get('CARGO_TARGET_DIR') or 'target') / 'debug' / 'gitpickle').resolve()
        command = [str(executable), 'mcp', '--profile', 'test'] if executable.exists() else [sys.executable, '-B', 'tools/gitpickle_mcp.py', '--profile', 'test']
        env = dict(os.environ, GITPICKLE_PROFILES=str(profile), TEST_AGENT_TOKEN='a' * 40)
        result = subprocess.run(command, input=''.join(json.dumps(r) + '\n' for r in requests),
            text=True, capture_output=True, env=env, timeout=15, cwd=self.root if executable.exists() else Path.cwd())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('', result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([1, 2, 3], [r['id'] for r in replies])
        self.assertEqual(m.VERSION, replies[0]['result']['protocolVersion'])
        self.assertEqual(7, len(replies[1]['result']['tools']))
        self.assertNotIn('a' * 40, result.stdout + result.stderr)

    def test_completed_backup_with_missing_lfs_stays_committed(self):
        self.configure()
        self.apply(self.preview())
        pointer = 'version https://git-lfs.github.com/spec/v1\noid sha256:' + 'a' * 64 + '\nsize 10\n'
        (self.source / 'large').write_text(pointer)
        a.git(self.source, 'add', 'large')
        a.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'LFS fixture')
        accepted = self.call('backup_run', repository='github-1', key='missing-lfs')
        self.jobs.start()
        self.jobs.follow(accepted['job_id'])
        result = self.call('jobs_get', job=accepted['job_id'], kind='backup')
        self.assertEqual('committed', result['data']['state'])
        coverage = self.call('protection_status', repository='github-1')
        self.assertFalse(coverage['data']['items'][0]['current_and_verified'])
        self.assertIn('incomplete_coverage', {g['code'] for g in coverage['coverage_gaps'] if g['component'] == 'lfs'})

    def test_protocol_bounds_and_error_redaction(self):
        self.configure()
        class Failure:
            def call(self, *_):
                raise RuntimeError('SECRET /private/path')
        self.protocol.client = Failure()
        result = self.call('protection_status')
        self.assertEqual('operational_failure', result['error']['code'])
        self.assertNotIn('SECRET', json.dumps(result))
        source, target = io.BytesIO(b'x' * (m.MAX_MESSAGE + 2)), io.StringIO()
        self.assertEqual(2, m.serve(Failure(), source, target))
        self.assertEqual(-32600, json.loads(target.getvalue())['error']['code'])
        target = io.StringIO()
        m.serve(Failure(), io.BytesIO(b'not-json\n[]\n'), target)
        self.assertEqual([-32700, -32600], [json.loads(line)['error']['code'] for line in target.getvalue().splitlines()])


if __name__ == '__main__':
    unittest.main()
