"""Calculate scoped protection evidence from immutable archive records."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import textwrap
import subprocess
import sys
import tempfile
import time

import gitpickle_archive as a
import gitpickle_recovery as recovery

EXCLUDED = ['issues', 'pull_requests', 'discussions', 'release_assets', 'packages', 'account_settings']
DEFAULT_FRESHNESS = 8 * 3600


class StatusError(Exception):
    def __init__(self, code, exit_code, action):
        self.code, self.exit_code, self.action = code, exit_code, action
        super().__init__(code)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise StatusError('invalid_input', 2, 'status')


def instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires a time zone')
    return parsed.timestamp()


def records(root, prefix, kind):
    directory = root / prefix
    if directory.is_symlink():
        raise ValueError('symlink in archive')
    for path in sorted(directory.iterdir()) if directory.exists() else []:
        key = prefix + '/' + path.name
        yield key, a.read(root, key, kind)


def protection_state(root, archive_id):
    values, children = {}, {}
    for key, value in records(root, 'protection/github', 'protection'):
        if value['archive_id'] != archive_id:
            raise ValueError('protection identity mismatch')
        if value['parent'] in children:
            raise StatusError('conflict', 4, 'inspect_archive')
        values[key], children[value['parent']] = value, key
    head, count, state = None, 0, {'repositories': {}, 'error': None}
    while head in children:
        head = children[head]
        state = values[head]['value']
        count += 1
    if count != len(values):
        raise StatusError('conflict', 4, 'inspect_archive')
    return state


def content_state(root, point):
    """Coverage repair alone does not imply a change to source content."""
    p = a.read(root, point, 'point')
    wiki = content_state(root, p['wiki']) if p.get('wiki') else p.get('wiki')
    return p['refs'], p['head'], p['lfs'], wiki


def stamp(at, repository, point, components, evidence_id):
    return {'at': at, 'repository_id': repository, 'point': point,
            'components': sorted(components), 'evidence_id': evidence_id}


def checkout_repository(checkout, selected, registered, by_id):
    try:
        remote = a.git(checkout, 'remote', 'get-url', 'origin').decode().strip()
    except subprocess.CalledProcessError:
        return None
    candidates = set()
    for repo_id in selected:
        registration = registered.get(repo_id, {})
        aliases = set(registration.get('aliases', []))
        if registration.get('alias'):
            aliases.add(registration['alias'])
        sources = {p['source'] for p in by_id.get(repo_id, [])}
        for alias in aliases:
            sources.update(('https://github.com/' + alias, 'git@github.com:' + alias,
                            'ssh://git@github.com/' + alias))
        if remote.rstrip('/').removesuffix('.git') in {
                source.rstrip('/').removesuffix('.git') for source in sources}:
            candidates.add(repo_id)
    if len(candidates) > 1:
        raise StatusError('conflict', 4, 'select_repository')
    return next(iter(candidates), None)


def calculate(root, repository=None, *, now=None, freshness=DEFAULT_FRESHNESS, checkout=None):
    """Read only. Discovery and job success never substitute for content evidence."""
    root = Path(root)
    now = time.time() if now is None else now
    if freshness <= 0 or not re.fullmatch('[A-Za-z0-9_-]+', repository or 'all'):
        raise StatusError('invalid_input', 2, 'status')
    if not (root / 'archive.json').exists():
        raise StatusError('configuration', 2, 'configure_archive')
    archive_id = a.read(root, 'archive.json', 'archive')['archive_id']
    state = protection_state(root, archive_id)
    registered = {r['repository_id']: r for r in state['repositories'].values() if r['protected']}
    inventory = recovery.storage_inventory(root, {repository} if repository else set(registered) or None)
    by_id = {r['repository_id']: r['points'] for r in inventory['repositories']}
    # Wiki repositories are components, not extra portfolio members.
    selected = set(registered) if registered else {r for r in by_id if not r.endswith('-wiki')}
    if repository is None and checkout is not None:
        repository = checkout_repository(checkout, selected, registered, by_id)
    if repository:
        if repository not in selected:
            raise StatusError('not_found', 5, 'repositories_list')
        selected = {repository}
    controls = {}
    for key, record in records(root, 'control', 'control'):
        if record['archive_id'] != archive_id:
            raise ValueError('control identity mismatch')
        if record['record_type'] in ('source_observation', 'source_tracking', 'capture_completed', 'source_failure'):
            controls.setdefault(record['payload']['repository_id'], []).append((key, record))
    results, gaps, evidence_ids = [], [], []
    for repo_id in sorted(selected):
        points = by_id.get(repo_id, [])
        latest = points[-1] if points else None
        point = latest['point'] if latest else None
        manifest = a.read(root, point, 'point') if point else None
        required = set(manifest['coverage']) if manifest else {'git', 'lfs'}
        registration = registered.get(repo_id)
        if registration and registration.get('has_wiki'):
            required.add('wiki')
        required.update(('git', 'lfs'))
        times = {'source_observation': None, 'content_change': None, 'capture_completion': None,
                 'verification': None, 'restore_test': None}
        observations, completion, failures = [], [], []
        for key, record in controls.get(repo_id, []):
            at = instant(record['recorded_at'])
            payload = record['payload']
            kind = record['record_type']
            if kind in ('source_observation', 'source_tracking'):
                observations.append((at, key, record))
            elif kind == 'source_failure':
                failures.append((at, payload['code']))
            elif payload['point'] == point:
                completion.append((at, key, record))
        coverage = {}
        repo_gaps = []
        publication_order = {p['point']: index for index, p in enumerate(points)}
        def matches(record):
            payload = record['payload']
            if record['record_type'] == 'source_observation':
                return payload['point'] == point
            return (manifest is not None and payload.get('head') == manifest['head']
                    and payload['refs'] == ''.join(ref + ' ' + oid + '\n'
                        for ref, oid in sorted(manifest['refs'].items())))

        def newest_observation(relevant):
            if not relevant:
                return None
            newest_time = max(item[0] for item in relevant)
            tied = [item for item in relevant if item[0] == newest_time]
            captures = [item for item in tied if item[2]['record_type'] == 'source_observation']
            tracking = [item for item in tied if item[2]['record_type'] == 'source_tracking']
            # Publication ancestry resolves equal capture times. Ref-query ties
            # have no such order, so any mismatch must remain visible.
            if captures:
                tracking.append(max(captures, key=lambda item: (
                    publication_order.get(item[2]['payload']['point'], -1), item[1])))
            return min(tracking, key=lambda item: (matches(item[2]), item[1]))

        def gap(component, code):
            item = {'repository_id': repo_id, 'component': component, 'code': code}
            if item not in repo_gaps:
                repo_gaps.append(item)
        if latest is None:
            gap('git', 'incomplete_coverage')
        # A Git-only ref query cannot refresh required LFS or wiki observations.
        for component in sorted(required):
            relevant = [(at, key, record) for at, key, record in observations
                        if component in record['payload'].get('components',
                            ['git'] if record['record_type'] == 'source_tracking' else
                            list(a.read(root, record['payload']['point'], 'point')['coverage']))]
            observation = newest_observation(relevant)
            matching, fresh, observed = False, False, None
            if observation:
                at, key, record = observation
                payload = record['payload']
                matching = matches(record)
                fresh = 0 <= now - at <= freshness
                observed = stamp(record['recorded_at'], repo_id, payload.get('point'), [component], key)
            if not fresh:
                gap(component, 'stale_coverage')
            if not matching:
                gap(component, 'state_mismatch')
            complete = False
            # Report each component independently even when another component is missing.
            if latest:
                complete = latest['coverage'].get(component) == 'complete' and not any(
                    (key.startswith(f'repos/{repo_id}/lfs/') if component == 'lfs'
                     else key.startswith(f'repos/{repo_id}/packs/') if component == 'git'
                     else key.startswith('repos/' + repo_id + '-wiki/')) for key in latest['missing'])
            if not complete:
                gap(component, 'incomplete_coverage')
            coverage[component] = {'required': True, 'complete': complete, 'source_observation': observed,
                                   'matches_observed_state': matching, 'fresh': fresh, 'verification': None}
        if observations:
            _, key, record = newest_observation(observations)
            times['source_observation'] = stamp(record['recorded_at'], repo_id,
                record['payload'].get('point'), record['payload'].get('components', ['git']), key)
        if completion:
            _, key, record = max(completion, key=lambda item: (item[0], item[1]))
            times['capture_completion'] = stamp(record['recorded_at'], repo_id, point,
                                                record['payload'].get('components', required), key)
        previous = None
        for key in a.inventory(root, repo_id):
            publication = a.read(root, key, 'publication')
            current = content_state(root, publication['point'])
            if current != previous:
                # Older archives without publication times remain explicitly unknown.
                times['content_change'] = stamp(publication['recorded_at'], repo_id,
                    publication['point'], a.read(root, publication['point'], 'point')['coverage'], key
                    ) if publication.get('recorded_at') else None
            previous = current
        restore_evidence = []
        verified_by_point = {p['point']: set() for p in points}
        for key, check in records(root, f'repos/{repo_id}/checks', 'evidence'):
            if check['archive_id'] != archive_id or check['repository_id'] != repo_id:
                raise ValueError('check identity mismatch')
            if check['point'] not in {p['point'] for p in points}:
                raise ValueError('check point mismatch')
            at = instant(check['recorded_at'])
            if check['passed'] is not True or check['reader_version'] != 1 or at > now:
                continue
            components = set(check['components'])
            allowed = {'lfs'} if check['method'] == 'lfs_digest' else (
                {'git', 'wiki'} if check['method'] == 'git_integrity' else
                {'git', 'lfs', 'wiki'} if check['method'] in ('archive_readback', 'independent_restore') else set())
            components &= allowed
            if not components:
                continue
            verified_by_point[check['point']].update(components)
            value = stamp(check['recorded_at'], repo_id, check['point'], components, key)
            if check['method'] == 'independent_restore':
                restore_evidence.append(value)
            if check['point'] == point:
                for component in components & required:
                    prior = coverage[component]['verification']
                    if prior is None or instant(prior['at']) < at:
                        coverage[component]['verification'] = value
        verifications = []
        for component, value in coverage.items():
            if value['verification'] is None:
                gap(component, 'unverified_coverage')
            else:
                verifications.append(value['verification'])
        if verifications:
            times['verification'] = max(verifications, key=lambda v: instant(v['at']))
        if restore_evidence:
            times['restore_test'] = max(restore_evidence, key=lambda v: instant(v['at']))
        observed_at = max((o[0] for o in observations), default=0)
        failure_at, failure_code = max(failures, default=(0, None))
        source_error = failure_code if failure_at >= observed_at else None
        if registration and (not registration['available'] or state['error']):
            source_error = state['error'] or 'source_unavailable'
        if source_error:
            gap('source', source_error)
        action = None
        if repo_gaps:
            action = {'operation': 'reconnect_source' if source_error in ('authentication', 'forbidden', 'source_unavailable')
                      else 'backup_run', 'arguments': {'repository_id': repo_id}}
        retained = next((p['point'] for p in reversed(points) if p['complete']
                         and set(p['coverage']) <= verified_by_point[p['point']]), None)
        results.append({'repository_id': repo_id, 'point': point, 'last_verified_point': retained,
            'current_and_verified': not repo_gaps, 'timestamps': times, 'coverage': coverage,
            'coverage_gaps': repo_gaps, 'restore_tests': restore_evidence, 'next_action': action})
        gaps.extend(repo_gaps)
        evidence_ids.extend(v['evidence_id'] for v in verifications + restore_evidence)
    healthy = bool(results) and not gaps
    return {'schema_version': 1, 'observed_at_unix_seconds': int(now),
        'scope': {'archive_id': archive_id, 'repository_ids': sorted(selected)},
        'outcome': 'ok', 'job_id': None, 'coverage_gaps': gaps,
        'evidence_ids': sorted(set(evidence_ids)),
        'next_action': next((r['next_action'] for r in results if r['next_action']),
                            None if results else {'operation': 'protect', 'arguments': {}}),
        'error': None, 'data': {'healthy': healthy, 'freshness_seconds': freshness,
            'current_and_verified': sum(r['current_and_verified'] for r in results),
            'need_attention': sum(not r['current_and_verified'] for r in results),
            'restore_tested_repository_ids': [r['repository_id'] for r in results if r['restore_tests']],
            'not_included': EXCLUDED, 'repositories': results}}


def restore_test(writer, repository, point):
    """Record a sampled restore only after archive-only reconstruction succeeds."""
    with writer.mutation(repository):
        root = writer.root
        if point not in [a.read(root, key, 'publication')['point'] for key in a.inventory(root, repository)]:
            raise ValueError('restore test point is not published for repository')
        with tempfile.TemporaryDirectory(dir=root,
                prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'gitpickle') + '-restore-test-') as staging:
            result = a.restore(root, point, Path(staging) / 'repository.git', allow_incomplete=True)
        return a.record(root, f'repos/{repository}/checks', {'kind': 'evidence',
            'archive_id': a.read(root, 'archive.json', 'archive')['archive_id'],
            'repository_id': repository, 'point': point, 'recorded_at': a.timestamp(),
            'method': 'independent_restore', 'components': result['verified_components'],
            'passed': result['complete'], 'reader_version': 1,
            'details': 'Sampled repository reconstructed in clean staging using archive bytes only'})


def render(result, attention=False, details=False):
    data = result['data']
    lines = ['Scope: ' + ', '.join(result['scope']['repository_ids']),
             f"{data['current_and_verified']} current and verified; {data['need_attention']} need attention",
             'Independent restore tests: ' + (', '.join(data['restore_tested_repository_ids']) or 'none')]
    observations = [component['source_observation'] for repo in data['repositories']
                    for component in repo['coverage'].values()]
    oldest = min((v['at'] for v in observations if v), key=instant, default='unknown')
    lines.append('Oldest source confirmation: ' + (oldest if all(observations) else 'unknown'))
    for repo in data['repositories']:
        if (attention or not details) and repo['current_and_verified']:
            continue
        lines.append(repo['repository_id'] + ': retained verified point ' + str(repo['last_verified_point']))
        for gap in repo['coverage_gaps']:
            messages = {'stale_coverage': 'Source confirmation is stale or missing.',
                        'state_mismatch': 'Captured data does not match the observed state.',
                        'incomplete_coverage': 'Required data is missing.',
                        'unverified_coverage': 'Matching verification evidence is missing.',
                        'authentication': 'Source authentication failed.',
                        'forbidden': 'Source access is denied.',
                        'source_unavailable': 'The source is unavailable.'}
            lines.append('  At risk: ' + gap['component'] + '. ' + messages.get(gap['code'], gap['code'])
                         + ' (' + gap['code'] + ')')
        for name, value in repo['timestamps'].items():
            scope = ('; point ' + str(value['point']) + '; components ' + ', '.join(value['components'])) if value else ''
            lines.append('  ' + name + ': ' + (value['at'] if value else 'unknown') + scope)
        if repo['next_action']:
            lines.append('  Next: ' + ('Reconnect source access for ' if repo['next_action']['operation'] == 'reconnect_source'
                                      else 'Retry backup for ') + repo['repository_id'] + '.')
    lines.append('GitHub metadata not included: ' + ', '.join(data['not_included']))
    if not data['repositories']:
        lines.append('Next: gitpickle protect github')
    width = max(20, min(80, shutil.get_terminal_size().columns))
    return '\n'.join(line for raw in lines for line in
                     (textwrap.wrap(raw, width=width, subsequent_indent='  ') or ['']))


def query_failure(error):
    if isinstance(error, StatusError):
        return error
    if isinstance(error, a.ArchiveConflict):
        return StatusError('conflict', 4, 'inspect_archive')
    if isinstance(error, a.UnsupportedRecord):
        return StatusError('unsupported_version', 7, 'upgrade_reader')
    if isinstance(error, PermissionError):
        return StatusError('forbidden', 3, 'check_archive_access')
    if isinstance(error, OSError):
        return StatusError('storage_unavailable', 5, 'check_archive_access')
    return StatusError('integrity_failure', 5, 'inspect_archive')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('command', choices=['status', 'doctor'])
    parser.add_argument('--archive', default=os.environ.get('GITPICKLE_ARCHIVE'))
    parser.add_argument('--repository')
    parser.add_argument('--all', action='store_true', help='Use the complete archive scope')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--attention', action='store_true')
    parser.add_argument('--freshness-seconds', type=int, default=DEFAULT_FRESHNESS)
    args = argparse.Namespace(json='--json' in (sys.argv[1:] if argv is None else argv), repository=None)
    try:
        args = parser.parse_args(argv)
        if not args.archive:
            raise StatusError('configuration', 2, 'configure_archive')
        if args.all and args.repository:
            raise StatusError('invalid_input', 2, 'select_repository')
        result = calculate(args.archive, args.repository, freshness=args.freshness_seconds,
                           checkout=None if args.all or args.repository else Path.cwd())
        print(json.dumps(result, sort_keys=True) if args.json else render(
            result, args.attention, details=args.command == 'doctor'))
        return 6 if (args.check or args.command == 'doctor') and not result['data']['healthy'] else 0
    except (StatusError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        error = query_failure(error)
        result = {'schema_version': 1, 'observed_at_unix_seconds': int(time.time()),
            'scope': {'archive_id': None, 'repository_ids': [args.repository] if args.repository else []},
            'outcome': 'failed', 'job_id': None, 'coverage_gaps': [], 'evidence_ids': [], 'data': None,
            'next_action': {'operation': error.action, 'arguments': {}},
            'error': {'code': error.code, 'message': 'Cannot query protection evidence.', 'retry': {'kind': 'never'}}}
        print(json.dumps(result, sort_keys=True), file=sys.stdout if args.json else sys.stderr)
        return error.exit_code


if __name__ == '__main__':
    sys.exit(main())
