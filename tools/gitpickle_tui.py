"""Opt-in terminal client. Application services own coverage and authorization."""
import argparse
import json
import os
from pathlib import Path
import sys
import textwrap
import uuid

from gitpickle_plans import PlanError
from gitpickle_workflow import Client, envelope, filter_rows, pages


def safe(value):
    # Repository text cannot emit terminal controls, bidi controls, or escapes.
    return str(value).encode('ascii', 'backslashreplace').decode().translate(
        {i: '?' for i in range(32)} | {127: '?'})




class Browser:
    """Testable navigation and review state; no terminal or authorization logic."""
    def __init__(self, client):
        self.client = client
        self.rows, self.visible = [], []
        self.query, self.mode = '', 'all'
        self.index, self.offset = 0, 0
        self.view, self.lines = 'portfolio', []
        self.points, self.point_index, self.point_focus = [], 0, False
        self.pending, self.review_seen = None, False
        self.message = ''
        self.repository = None

    def refresh(self):
        self.rows = self.client.call('portfolio', {})
        self.select()

    def select(self):
        self.visible = filter_rows(self.rows, self.query, self.mode)
        self.index = min(self.index, max(0, len(self.visible) - 1))

    def show(self, view, value):
        self.view, self.offset = view, 0
        self.lines = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True).splitlines()

    def details(self, repository=None):
        if repository is not None:
            matches = [i for i, row in enumerate(self.visible) if row['repository_id'] == repository]
            if not matches:
                self.back()
                self.repository = None
                raise PlanError('forbidden')
            self.index = matches[0]
        if self.visible:
            row = self.visible[self.index]
            self.repository = row['repository_id']
            try:
                hosting = self.client.call('hosting_status', dict(repository=self.repository))
            except PlanError as error:
                if error.code != 'forbidden':
                    raise
                hosting = dict(mode='unavailable', reason='Hosting inspection requires a git_read grant.')
            self.show('details', dict(backup_coverage=row, hosting=hosting))
            self.lines[:0] = [
                'Repository: ' + self.repository,
                'Backup coverage: ' + ('current and verified' if row['current_and_verified'] else 'needs attention'),
                'Hosting mode: ' + hosting['mode'],
                'Remote URL: ' + (hosting.get('remote_url') or 'unavailable'),
                '',
            ]

    def recovery_points(self):
        self.points = pages(self.client, 'agent_recovery_points_list', dict(repository=self.repository))
        self.point_index, self.point_focus = 0, True
        self.view, self.offset = 'points', 0

    def history(self):
        self.show('jobs', self.client.call('repository_jobs', dict(repository=self.repository)))

    def capture(self, operation):
        preview = self.client.call('capture_preview', dict(repository=self.repository,
            all=False, operation=operation, key=uuid.uuid4().hex))
        self.pending = ('capture', preview)
        self.review_seen = False
        self.show('review', dict(scope=[self.repository], operation=operation,
            consequences='Capture source data. Retained points are not deleted. Server work continues after exit.',
            authority=operation + ' grant; rechecked on acceptance', preview=preview))

    def restore(self, destination):
        import time
        args = dict(repository=self.repository, destination=str(Path(destination).absolute()),
                    expires_at=int(time.time()) + 3600, maximum_bytes=2**63-1)
        if self.view == 'points' and self.points:
            args['point'] = self.points[self.point_index]['point']
        preview = self.client.call('restore_plan', args)
        self.pending = ('restore', preview)
        self.review_seen = False
        self.show('review', dict(scope=[self.repository], operation='restore',
            consequences='Recover into a new directory. No existing files are overwritten.',
            authority='Trusted operator approval and apply_plan grant; rechecked on execution', preview=preview))

    def apply(self):
        if self.view != 'review' or not self.pending or not self.review_seen:
            return
        kind, preview = self.pending
        if kind == 'capture':
            result = self.client.call('capture_accept', dict(reference=preview['reference'], digest=preview['digest']))
        else:
            approval = self.client.call('approve_plan', dict(reference=preview['reference'], digest=preview['plan']['digest']))
            job = self.client.call('plan_apply', dict(plan=preview['plan'], approval=approval))
            result = dict(restore_job=job)
        self.pending = None
        self.show('accepted', result)

    def back(self):
        self.pending = None
        self.view, self.offset = 'portfolio', 0

    def content(self, width, height):
        width, height = max(1, width), max(1, height)
        if self.view == 'portfolio':
            rows = ['{} {} {}'.format('>' if i == self.index else ' ', r['repository_id'],
                ('verified' if r['current_and_verified'] else 'attention') + ' ' + ' '.join(r.get('aliases', [])))
                for i, r in enumerate(self.visible)] or ['No matching repositories']
            start = max(0, self.index - height + 1)
            return [safe(s)[:width] for s in rows[start:start + height]]
        if self.view == 'points':
            rows = [('> ' if i == self.point_index else '  ') + safe(p['point']) +
                    ' complete=' + str(p['complete']) + ' verified=' + str(p['verified'])
                    for i, p in enumerate(self.points)] or ['No recovery points']
            # Full immutable keys and evidence remain visible on narrow screens.
            self.lines = rows
            if self.point_focus:
                self.offset = sum(max(1, len(textwrap.wrap(safe(line), width,
                    replace_whitespace=False, drop_whitespace=False))) for line in rows[:self.point_index])
                self.point_focus = False
        wrapped = [part for line in self.lines for part in (textwrap.wrap(safe(line), width,
            replace_whitespace=False, drop_whitespace=False) or [''])]
        self.offset = min(self.offset, max(0, len(wrapped) - height))
        if self.view == 'review' and self.offset + height >= len(wrapped):
            self.review_seen = True
        return wrapped[self.offset:self.offset + height]


def edit(screen, label):
    """Keyboard-only editor. Escape cancels. Never enable terminal echo."""
    import curses
    value = ''
    while True:
        h, w = screen.getmaxyx()
        try:
            screen.addnstr(max(0, h - 1), 0, safe(label + value)[-max(1, w - 1):], max(0, w - 1))
            screen.clrtoeol()
            screen.refresh()
        except curses.error:
            pass
        key = screen.get_wch()
        if key == '\x1b':
            return None
        if key in ('\n', '\r', curses.KEY_ENTER):
            return value
        if key in ('\b', '\x7f', curses.KEY_BACKSPACE):
            value = value[:-1]
        elif isinstance(key, str) and key.isprintable() and len(value) < 4096:
            value += key


def run(screen, browser):
    import curses
    screen.keypad(True)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    browser.refresh()
    while True:
        screen.erase()
        h, w = screen.getmaxyx()
        if h < 6 or w < 20:
            try:
                screen.addnstr(0, 0, 'Resize to 20x6; q exits', max(0, w - 1))
                screen.refresh()
            except curses.error:
                pass
            if screen.get_wch() in ('q', '\x03'):
                return
            continue
        verified = sum(row['current_and_verified'] for row in browser.rows)
        title = f'GitPickle | {browser.view} | {len(browser.rows)} repositories | Coverage: {verified} verified'
        scope = f'Search: {browser.query} | Filter: {browser.mode} | Times: Unix seconds UTC'
        help_line = ('j/k move / search f filter Enter open r refresh ? help q quit' if browser.view == 'portfolio'
            else 'Esc back j/k scroll v points n/p select b backup s sync o restore h jobs r refresh q quit')
        if browser.view == 'review':
            help_line = 'Read full plan: j/k PgDn/PgUp | y apply | Esc cancel | q quit'
        secondary = help_line
        if w < 60:
            title = 'GitPickle ' + browser.view
            scope = f'{len(browser.visible)} shown | {browser.mode}'
            if browser.view == 'portfolio':
                help_line, secondary = 'j/k / f Enter ? q', 'r refresh; ? help'
            elif browser.view == 'review':
                help_line, secondary = 'j/k read; y apply', 'Esc cancel; q quit'
            else:
                help_line, secondary = 'v h b s o ? Esc q', 'j/k scroll; r load'
        lines = [title, scope] + browser.content(max(1, w - 1), max(1, h - 4))
        lines += [''] * max(0, h - 2 - len(lines))
        lines += [browser.message or help_line, secondary]
        for y, line in enumerate(lines[:h]):
            try:
                screen.addnstr(y, 0, safe(line), max(0, w - 1))
            except curses.error:
                pass
        screen.refresh()
        key = screen.get_wch()
        browser.message = ''
        try:
            if key in ('q', '\x03'):
                return
            if key in ('\x1b', '\b', curses.KEY_BACKSPACE):
                browser.back()
            elif key in ('j', curses.KEY_DOWN, 'k', curses.KEY_UP, curses.KEY_NPAGE, curses.KEY_PPAGE):
                delta = -1 if key in ('k', curses.KEY_UP, curses.KEY_PPAGE) else 1
                delta *= max(1, h - 4) if key in (curses.KEY_NPAGE, curses.KEY_PPAGE) else 1
                if browser.view == 'portfolio':
                    browser.index = max(0, min(len(browser.visible) - 1, browser.index + delta))
                else:
                    browser.offset = max(0, browser.offset + delta)
            elif key == '/' and browser.view == 'portfolio':
                query = edit(screen, 'Search: ')
                if query is not None:
                    browser.query, browser.index = query, 0
                    browser.select()
            elif key == 'f' and browser.view == 'portfolio':
                browser.mode = {'all': 'attention', 'attention': 'verified', 'verified': 'all'}[browser.mode]
                browser.index = 0
                browser.select()
            elif key in ('\n', '\r', curses.KEY_ENTER) and browser.view == 'portfolio':
                browser.details()
            elif key == 'r':
                if browser.view == 'portfolio':
                    browser.refresh()
                elif browser.view == 'jobs':
                    browser.history()
                elif browser.view == 'points':
                    browser.recovery_points()
                elif browser.view == 'details':
                    repository = browser.repository
                    browser.refresh()
                    browser.details(repository)
            elif key == '?' and browser.view != 'review':
                browser.show('help', dict(keys=dict(move='arrows, j/k; Page Up/Down scroll', search='/ then Enter; Escape cancels',
                    filter='f cycles all, attention, verified', details='Enter opens repository', points='v lists points; n/p select',
                    jobs='h lists jobs; r refreshes', operations='b backup, s sync, o restore, y approve after full review',
                    back='Escape returns to portfolio', quit='q or Ctrl-C exits; accepted jobs continue'), cli='portfolio, status, hosting, points, history, jobs, backup, sync, restore',
                    coverage='Backup coverage and Git hosting are independent. Only retained data is recoverable.',
                    operations='Select a repository first. Restore uses the newest complete verified point unless selected in points.'))
            elif key == 'y' and browser.view == 'review':
                browser.apply()
            elif browser.repository and browser.view in ('details', 'points', 'jobs', 'accepted'):
                if key == 'v':
                    browser.recovery_points()
                elif key in ('n', 'p') and browser.view == 'points':
                    browser.point_index = max(0, min(len(browser.points) - 1, browser.point_index + (1 if key == 'n' else -1)))
                    browser.point_focus = True
                elif key == 'h':
                    browser.history()
                elif key in ('b', 's'):
                    browser.capture('backup' if key == 'b' else 'sync')
                elif key == 'o':
                    destination = edit(screen, 'New restore directory (Esc cancels): ')
                    if destination:
                        browser.restore(destination)
        except (PlanError, ValueError, OSError) as error:
            browser.message = 'Operation failed: ' + safe(getattr(error, 'code', 'operational_failure'))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Check automation before importing curses or touching the terminal.
    if '--json' in argv:
        print(json.dumps(envelope(error='interactive_only'), sort_keys=True))
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default=os.environ.get('GITPICKLE_SERVER'))
    args = parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('TUI requires a terminal. Use gitpickle portfolio --json or gitpickle help --all.', file=sys.stderr)
        return 2
    try:
        if not args.server:
            raise PlanError('configuration')
        token = os.environ.get('GITPICKLE_MANAGEMENT_TOKEN')
        if not token:
            import getpass
            token = getpass.getpass('Management credential: ')
        if not token:
            raise PlanError('authentication')
        import curses
        # No color pairs are used. NO_COLOR needs no special terminal behavior.
        try:
            curses.wrapper(run, Browser(Client(args.server, token)))
        except curses.error:
            print('Cannot open this terminal. Use the plain CLI.', file=sys.stderr)
            return 5
        return 0
    except KeyboardInterrupt:
        return 0
    except (PlanError, OSError, ValueError) as error:
        print('TUI failed: ' + safe(getattr(error, 'code', 'operational_failure')), file=sys.stderr)
        return 5
    except ImportError:
        print('TUI requires Python curses. Use the plain CLI.', file=sys.stderr)
        return 5


if __name__ == '__main__':
    sys.exit(main())
