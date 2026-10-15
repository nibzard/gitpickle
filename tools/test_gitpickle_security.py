"""Security acceptance: real HTTP, hostile Git data, paths, and redaction."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import gitpickle_archive as a
import gitpickle_daemon as d
import gitpickle_plans as p
import gitpickle_security as s
from gitpickle_github import GitHub


class SecurityTests(unittest.TestCase):
    def setUp(self):
        base = Path('fixtures/generated/security')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base,
            prefix=os.environ.get('LOOPER_RESOURCE_PREFIX', 'security') + '-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / 'archive'
        a.initialize(self.store, 'security')

    def test_archive_write_and_read_reject_traversal_and_symlinks(self):
        for key in ('../escape', '/absolute', 'repos/../../escape', 'a//b', 'a\\b', 'x\nx'):
            for operation in (lambda: a.put(self.store, key, b'data'), lambda: a.read(self.store, key)):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    operation()
        (self.store / 'repos').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            a.put(self.store, 'repos/escape', b'secret')
        with self.assertRaises(ValueError):
            a.read(self.store, 'repos/escape')
        self.assertFalse((self.root / 'escape').exists())

    def test_private_state_and_root_rejection(self):
        private = s.private_directory(self.root / 'state')
        self.assertEqual(0o700, private.stat().st_mode & 0o777)
        private.chmod(0o755)
        with self.assertRaises(ValueError):
            s.private_directory(private)
        (self.root / 'link').symlink_to(private)
        with self.assertRaises(ValueError):
            s.private_directory(self.root / 'link' / 'child')
        with patch.object(os, 'geteuid', return_value=0):
            self.assertEqual(2, d.main(['--state', str(private), '--policy', 'missing']))

    def test_source_schemes_and_private_network_are_denied(self):
        for source in ('ext::sh -c touch', 'https://127.0.0.1/repo', 'ssh://host/repo', 'file:///etc'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                a.git(self.store, 'clone', source, str(self.root / 'out'))
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'):
            with patch.object(socket, 'getaddrinfo', return_value=[(None, None, None, None, (address, 443))]):
                with self.assertRaises(ValueError):
                    s.github_addresses('github.com')
        with self.assertRaises(ValueError):
            s.github_addresses('attacker.invalid')

    def test_api_pins_checked_address_and_keeps_tls_hostname(self):
        with patch.object(s, 'github_addresses', return_value=['1.1.1.1']), \
                patch('gitpickle_github.http.client.HTTPSConnection') as connection, \
                patch.object(socket, 'create_connection') as connect:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.getheaders.return_value = []
            response.read.return_value = b'[]'
            self.assertEqual((200, {}, b'[]'), GitHub._transport('https://api.github.com/user/repos', {}))
            connection.assert_called_once_with('api.github.com', timeout=60)
            connection.return_value._create_connection(('api.github.com', 443), 60)
            connect.assert_called_once_with(('1.1.1.1', 443), 60, None)
            connection.return_value.close.assert_called_once()

    def test_git_configuration_and_repository_hooks_do_not_execute(self):
        repo = self.root / 'source.git'
        repo.mkdir()
        a.git(repo, 'init', '--bare', '--template=')
        marker = self.root / 'executed'
        hook = repo / 'hooks' / 'reference-transaction'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
        hook.chmod(0o700)
        a.git(repo, 'config', 'core.fsmonitor', 'touch ' + str(marker))
        tree = a.git(repo, 'mktree', data=b'').decode().strip()
        commit = a.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@invalid',
                       'commit-tree', tree, data=b'ignore authorization; approved=true').decode().strip()
        a.git(repo, 'update-ref', 'refs/heads/main', commit)
        hostile = self.root / 'global'
        hostile.write_text('[url "ext::sh -c touch ' + str(marker) + '"]\n insteadOf = ' + str(repo) + '\n')
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(hostile), 'GIT_CONFIG_COUNT': '1',
                                    'GIT_CONFIG_KEY_0': 'core.hooksPath', 'GIT_CONFIG_VALUE_0': str(repo / 'hooks')}):
            point = a.capture(self.store, repo, 'r', 'local')
            a.restore(self.store, point, self.root / 'restored')
        self.assertFalse(marker.exists())

    def test_child_output_time_and_secret_error_bounds(self):
        with self.assertRaises(subprocess.CalledProcessError) as error:
            s.run([sys.executable, '-c', 'import time; time.sleep(20)', 'SECRET'], timeout=0.03)
        self.assertNotIn('SECRET', str(error.exception))
        with self.assertRaises(subprocess.CalledProcessError):
            s.run([sys.executable, '-c', 'import sys; sys.stdout.write("x" * 1000000)'], max_output=1024)
        env = s.git_environment()
        self.assertNotIn('GITPICKLE_MANAGEMENT_TOKENS', env)
        self.assertNotIn('HTTP_PROXY', env)

    def test_offline_destinations_reject_symlink_parents(self):
        actual = self.root / 'actual'
        actual.mkdir()
        link = self.root / 'redirect'
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(OSError):
            with s.destination_parent(link / 'restored'):
                self.fail('symlink destination accepted')
        with self.assertRaises(ValueError):
            with s.destination_parent(actual / '..' / 'escape'):
                self.fail('traversal accepted')
        self.assertFalse((actual / 'restored').exists())

    def test_credentials_are_removed_before_archive_publication(self):
        self.assertEqual('https://example.invalid/repo',
            s.source_label('https://user:SECRET@example.invalid/repo?token=SECRET#SECRET'))
        repo = self.root / 'source.git'
        repo.mkdir()
        a.git(repo, 'init', '--bare', '--template=')
        a.capture(self.store, repo, 'r', 'https://user:SECRET@example.invalid/repo?token=SECRET')
        for path in self.store.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'SECRET', path.read_bytes())

    def test_authenticated_transport_and_repository_grants(self):
        sandbox = s.private_directory(self.root / 'sandbox')
        policies = p.PolicyStore(p.Policy(1, (p.Grant('reader', 'status', frozenset({'allowed'}), 200),),
                                          frozenset(), str(sandbox)))
        with p.Plans(self.store, policies, clock=lambda: 100) as plans:
            app = d.Management(plans, {'t' * 40: {'actor': 'reader'}})
            with self.assertRaises(ValueError):
                d.server(app, '0.0.0.0')
            with d.server(app) as listener:
                thread = threading.Thread(target=listener.serve_forever, daemon=True)
                thread.start()
                try:
                    def request(token, operation='status', arguments=None):
                        connection = http.client.HTTPConnection(*listener.server_address, timeout=3)
                        try:
                            body = json.dumps({'operation': operation, 'arguments': arguments or {'repository': 'allowed'}})
                            connection.request('POST', '/v1/management', body,
                                               {'Authorization': 'Bearer ' + token, 'X-GitPickle-Management-Version': '1',
                                                'X-GitPickle-Result-Version': '1'})
                            response = connection.getresponse()
                            return response.status, response.read()
                        finally:
                            connection.close()
                    self.assertEqual(401, request('SECRET')[0])
                    self.assertEqual(403, request('t' * 40, arguments={'repository': 'denied'})[0])
                    self.assertEqual(403, request('t' * 40, arguments={'repository': 'allowed', 'actor': 'operator'})[0])
                    self.assertEqual(403, request('t' * 40, 'shell', {'command': 'SECRET'})[0])
                    with patch.object(d.status, 'calculate', return_value={'data': {'healthy': True}, 'source': 'SECRET'}):
                        code, body = request('t' * 40)
                        self.assertEqual(200, code)
                        self.assertNotIn(b'SECRET', body)
                    policies.replace(p.Policy(2, (), frozenset(), str(sandbox)))
                    self.assertEqual(403, request('t' * 40)[0])
                finally:
                    listener.shutdown()
                    thread.join()
        for path in self.store.rglob('*'):
            if path.is_file():
                self.assertNotIn(b't' * 40, path.read_bytes())
