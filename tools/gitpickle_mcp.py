"""Local newline-delimited MCP transport. No terminal output on stdout."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

from gitpickle_agent import SCHEMAS, DESCRIPTIONS, validate
from gitpickle_plans import PlanError
from gitpickle_workflow import Client, envelope

VERSION = '2025-11-25'
MAX_MESSAGE = 65536


def configuration(existing, profile, executable):
    """Preview an additive merge. Never write or replace client settings."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', profile) or not isinstance(existing, dict):
        raise PlanError('configuration')
    result = dict(existing)
    servers = result.get('mcpServers', {})
    if not isinstance(servers, dict) or 'gitpickle' in servers:
        raise PlanError('configuration_conflict')
    result['mcpServers'] = dict(servers, gitpickle=dict(command=executable, args=['mcp', '--profile', profile]))
    return result


def profile_client(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name):
        raise PlanError('configuration')
    path = Path(os.environ.get('GITPICKLE_PROFILES', Path.home() / '.config/gitpickle/profiles.json'))
    value = json.loads(path.read_text())
    selected = value['profiles'][name]
    if set(selected) != {'server', 'token_env'} or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', selected['token_env']):
        raise PlanError('configuration')
    token = os.environ.pop(selected['token_env'], '')
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise PlanError('authentication')
    return Client(selected['server'], token)


class Protocol:
    def __init__(self, client):
        self.client, self.initialized, self.ready = client, False, False

    def dispatch(self, request):
        request_id = request.get('id') if isinstance(request, dict) else None
        def error(code, message):
            return dict(jsonrpc='2.0', id=request_id, error=dict(code=code, message=message))
        if (not isinstance(request, dict) or request.get('jsonrpc') != '2.0'
                or not isinstance(request.get('method'), str)
                or ('id' in request and type(request_id) not in (int, str))):
            return error(-32600, 'Invalid request')
        method, params = request['method'], request.get('params', {})
        if 'id' not in request:
            if method == 'notifications/initialized' and self.initialized:
                self.ready = True
            return None
        if not isinstance(params, dict):
            return error(-32602, 'Invalid parameters')
        if method == 'initialize':
            if self.initialized or not isinstance(params.get('protocolVersion'), str) or not isinstance(params.get('capabilities'), dict) or not isinstance(params.get('clientInfo'), dict):
                return error(-32602, 'Invalid initialization')
            self.initialized = True
            result = dict(protocolVersion=VERSION, capabilities=dict(tools=dict(listChanged=False)),
                serverInfo=dict(name='gitpickle', version=os.environ.get('GITPICKLE_SOFTWARE_VERSION', 'development')))
        elif method == 'ping':
            result = {}
        elif not self.ready:
            return error(-32600, 'Initialize the session first')
        elif method == 'tools/list':
            if params:
                return error(-32602, 'Invalid parameters')
            result = dict(tools=[dict(name=name, description=DESCRIPTIONS[name], inputSchema=spec,
                annotations=dict(readOnlyHint=name in ('protection_status', 'repositories_list', 'recovery_points_list', 'jobs_get'),
                    destructiveHint=False, openWorldHint=False, idempotentHint=name not in ('plan_apply', 'restore_plan')))
                for name, spec in SCHEMAS.items()])
        elif method == 'tools/call':
            if set(params) - {'name', 'arguments', '_meta'} or not isinstance(params.get('name'), str) or params['name'] not in SCHEMAS:
                return error(-32602, 'Unknown tool or invalid parameters')
            try:
                validate(params['name'], params.get('arguments', {}))
            except PlanError:
                return error(-32602, 'Invalid tool arguments')
            try:
                value = self.client.call('agent_' + params['name'], params.get('arguments', {}))
            except Exception as failure:
                # Fixed local codes only. Never echo remote diagnostics or inputs.
                allowed = {'forbidden', 'authentication', 'approval_required', 'conflict', 'expired_plan',
                    'unsafe_destination', 'impact_limit', 'replayed_plan', 'invalid_input', 'invalid_scope',
                    'cursor_stale', 'unsupported_capability', 'unsupported_version', 'configuration'}
                code = failure.code if isinstance(failure, PlanError) and failure.code in allowed else 'operational_failure'
                value = envelope(error=code)
                value['next_action'] = dict(operation='check_authorization' if code in ('forbidden', 'approval_required') else 'inspect_configuration', arguments={})
            result = dict(content=[dict(type='text', text=json.dumps(value, sort_keys=True, ensure_ascii=True))],
                          structuredContent=value, isError=value.get('error') is not None)
        else:
            return error(-32601, 'Method not found')
        return dict(jsonrpc='2.0', id=request_id, result=result)


def serve(client, source, target):
    protocol = Protocol(client)
    while True:
        line = source.readline(MAX_MESSAGE + 1)
        if not line:
            return 0
        if len(line) > MAX_MESSAGE:
            # End the connection after one bounded error; do not process fragments.
            target.write(json.dumps(dict(jsonrpc='2.0', id=None, error=dict(code=-32600, message='Message too large'))) + '\n')
            target.flush()
            return 2
        try:
            request = json.loads(line)
            reply = protocol.dispatch(request)
        except (ValueError, UnicodeError):
            reply = dict(jsonrpc='2.0', id=None, error=dict(code=-32700, message='Parse error'))
        except Exception:
            reply = dict(jsonrpc='2.0', id=None, error=dict(code=-32603, message='Internal error'))
        if reply is not None:
            encoded = json.dumps(reply, sort_keys=True, ensure_ascii=True)
            if len(encoded.encode()) > MAX_MESSAGE:
                encoded = json.dumps(dict(jsonrpc='2.0', id=reply['id'], error=dict(code=-32603, message='Result exceeds limit')))
            target.write(encoded + '\n')
            target.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--config-preview', metavar='EXISTING_JSON')
    args = parser.parse_args(argv)
    try:
        if args.config_preview:
            existing = json.loads(Path(args.config_preview).read_text())
            print(json.dumps(configuration(existing, args.profile, os.environ.get('GITPICKLE_EXECUTABLE', 'gitpickle')), sort_keys=True))
            return 0
        return serve(profile_client(args.profile), sys.stdin.buffer, sys.stdout)
    except Exception:
        print('configuration: Check the selected profile and runtime credential.', file=sys.stderr)
        return 2
