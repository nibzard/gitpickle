"""Terminal navigation, plain CLI parity, and application authorization acceptance."""
import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import gitpickle_archive as a
from gitpickle_plans import Grant, PlanError, Policy, Principal
import gitpickle_tui as t
import test_gitpickle_workflow as fixture


class TuiTests(unittest.TestCase):
    setUp = fixture.WorkflowTests.setUp
    preview = fixture.WorkflowTests.preview
    apply = fixture.WorkflowTests.apply
    command = fixture.WorkflowTests.command

    def browser(self):
        self.apply(self.preview())
        self.plans.writer.capture(self.source, 'github-1', 'github')
        browser = t.Browser(self.client)
        browser.refresh()
        return browser

    def test_search_filter_details_and_plain_cli(self):
        browser = self.browser()
        self.assertEqual('github-1', browser.visible[0]['repository_id'])
        browser.query = 'alice/one'
        browser.select()
        self.assertEqual(1, len(browser.visible))
        browser.query = 'missing'
        browser.select()
        self.assertEqual(['No matching repositories'], browser.content(30, 4))
        browser.query = ''
        browser.select()
        browser.details()
        text = '\n'.join(browser.lines)
        self.assertIn('backup_coverage', text)
        self.assertIn('hosting', text)
        self.assertIn('disabled', text)
        browser.recovery_points()
        self.assertTrue(browser.points)
        for width in (1, 19, 30, 79):
            self.assertTrue(all(len(line) <= width for line in browser.content(width, 2)))
        browser.history()
        self.assertEqual('jobs', browser.view)
        for args in (['portfolio', '--search', 'alice/one'], ['points', '--repository', 'github-1'],
                     ['history', '--repository', 'github-1']):
            code, output, _ = self.command(args + ['--json'])
            self.assertEqual(0, code)
            self.assertEqual(1, json.loads(output)['schema_version'])

    def test_review_cancel_apply_and_historical_restore(self):
        browser = self.browser()
        browser.details()
        browser.capture('backup')
        with patch.object(self.client, 'call', side_effect=AssertionError('unreviewed apply')):
            browser.apply()
        browser.back()
        self.assertIsNone(browser.pending)
        browser.details()
        browser.capture('sync')
        while not browser.review_seen:
            browser.content(30, 3)
            browser.offset += 3
        browser.apply()
        self.assertEqual('accepted', browser.view)
        (self.source / 'new-file').write_text('historical change')
        a.git(self.source, 'add', 'new-file')
        a.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'new point')
        self.plans.writer.capture(self.source, 'github-1', 'github')
        browser.recovery_points()
        self.assertEqual(2, len(browser.points))
        point = browser.points[0]['point']
        destination = str(self.root / 'historical-restored')
        browser.restore(destination)
        self.assertEqual(point, browser.pending[1]['plan']['recovery']['point'])
        self.assertFalse(browser.pending[1]['plan']['overwrite'])
        while not browser.review_seen:
            browser.content(30, 3)
            browser.offset += 3
        browser.apply()
        self.assertEqual('accepted', browser.view)
        job = json.loads('\n'.join(browser.lines))['restore_job']
        self.workflow.restore_futures[job].result(timeout=10)
        self.assertEqual('completed', self.client.call('job_get', dict(job=job)))
        self.assertEqual(b'data', a.git(Path(destination), 'show', 'refs/heads/main:committed'))
        self.assertNotIn(b'new-file', a.git(Path(destination), 'ls-tree', '--name-only', 'refs/heads/main'))
        browser.history()
        self.assertIn('restore', '\n'.join(browser.lines))
        code, output, _ = self.command(['restore', 'github-1', '--point', point,
            '--destination', str(self.root / 'cli-restore'), '--preview', '--json'])
        self.assertEqual(0, code)
        self.assertEqual(point, json.loads(output)['data']['plan']['recovery']['point'])

    def test_authorization_revoked_after_review_and_agent_denied(self):
        browser = self.browser()
        browser.details()
        browser.capture('backup')
        browser.review_seen = True
        self.policies.replace(Policy(2, (), frozenset(), str(self.root)))
        with self.assertRaises(PlanError):
            browser.apply()
        with self.assertRaises(PlanError):
            self.client.call('hosting_status', dict(repository='github-1'))
        self.assertEqual([], self.client.call('portfolio', {}))
        with self.assertRaises(PlanError):
            self.workflow.call(Principal('owner', True), 'portfolio', {})

    def test_automation_never_imports_curses_or_starts_terminal(self):
        for args in ([], ['--json']):
            output = io.StringIO()
            with patch.dict('sys.modules', curses=None), contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(io.StringIO()), patch('sys.stdin.isatty', return_value=False):
                self.assertEqual(2, t.main(args))
            if args:
                self.assertEqual('interactive_only', json.loads(output.getvalue())['error']['code'])
        self.assertNotIn('\x1b', t.safe('\x1b[31m\n\u202e'))

    def test_scripted_keyboard_and_small_terminal(self):
        browser = self.browser()
        class Screen:
            def __init__(inner):
                inner.keys = iter(['/', 'a', 'l', 'i', '\n', 'f', 'f', 'f', '\n', 'v', 'n', 'p', '\x1b', '?', '\x1b', 'q'])
                inner.paints = []
            def keypad(inner, *_): pass
            def getmaxyx(inner): return (8, 30)
            def erase(inner): pass
            def addnstr(inner, y, x, text, n):
                self.assertLess(y, 8)
                self.assertLessEqual(n, 29)
                inner.paints.append(text[:n])
            def refresh(inner): pass
            def clrtoeol(inner): pass
            def get_wch(inner): return next(inner.keys)
        screen = Screen()
        with patch('curses.curs_set'):
            t.run(screen, browser)
        self.assertTrue(screen.paints)
        self.assertTrue(any('points' in line for line in screen.paints))
        self.assertFalse(any('\x1b' in line for line in screen.paints))

    def test_scope_does_not_expand_on_refresh(self):
        browser = self.browser()
        browser.details()
        browser.visible = []
        with self.assertRaises(PlanError):
            browser.details('github-1')
        self.assertIsNone(browser.repository)
        self.assertEqual('portfolio', browser.view)
        with self.assertRaises(PlanError):
            self.client.call('repository_jobs', dict(repository='github-ungranted'))

    def test_point_selection_stays_visible_when_wrapped(self):
        browser = t.Browser(None)
        browser.view = 'points'
        browser.points = [dict(point='repos/repo/points/' + str(i) * 64, complete=True, verified=True) for i in range(20)]
        browser.point_index, browser.point_focus = 15, True
        lines = browser.content(19, 3)
        self.assertTrue(lines[0].startswith('> '))
        self.assertGreater(browser.offset, 15)

    def test_tiny_terminal_requires_resize_before_operations(self):
        browser = t.Browser(None)
        class Screen:
            def keypad(inner, *_): pass
            def getmaxyx(inner): return (4, 12)
            def erase(inner): pass
            def addnstr(inner, y, x, text, n): self.assertLessEqual(n, 11)
            def refresh(inner): pass
            def get_wch(inner): return 'q'
        with patch.object(browser, 'refresh'), patch.object(browser, 'content', side_effect=AssertionError('hidden content')), \
                patch('curses.curs_set'):
            t.run(Screen(), browser)

    def test_backup_details_survive_missing_hosting_grant(self):
        browser = self.browser()
        with patch.object(self.client, 'call', side_effect=PlanError('forbidden')):
            browser.details()
        self.assertEqual('details', browser.view)
        self.assertIn('backup_coverage', '\n'.join(browser.lines))
        self.assertIn('unavailable', '\n'.join(browser.lines))

    def test_portfolio_returns_only_current_grants(self):
        self.apply(self.preview(all=True))
        self.policies.replace(Policy(2, (Grant('owner', 'status', frozenset({'github-1'}), 2**62),),
            frozenset(), str(self.root)))
        self.assertEqual(['github-1'], [r['repository_id'] for r in self.client.call('portfolio', {})])
        self.assertEqual(3, self.command(['points', '--repository', 'github-2', '--json'])[0])


if __name__ == '__main__':
    unittest.main()
