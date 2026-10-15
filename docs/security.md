# Security boundaries

`gitpickle serve` runs an authenticated local management service. It requires
Python 3, native Git, and Linux `prlimit` from util-linux. It refuses root.
Run it as a dedicated service user. The archive and restore sandbox must belong
to that user and have mode `0700`. The policy file must have mode `0600`.
The service rejects symlinks in state paths. It sets the process umask to `0077`.

The listener defaults to `127.0.0.1` and a free port. Startup prints that port.
Non-loopback addresses are rejected. Use an authenticated SSH tunnel for remote
management. Public transport and TLS termination are outside this listener.

## Configuration

Keep the policy outside the archive. Use this nonsecret JSON structure:

```json
{
  "revision": 1,
  "grants": [
    {"actor": "reader", "permission": "status", "repositories": ["github-123"], "expires_at": 2000000000},
    {"actor": "agent", "permission": "plan_restore", "repositories": ["github-123"], "expires_at": 2000000000},
    {"actor": "agent", "permission": "apply_plan", "repositories": ["github-123"], "expires_at": 2000000000}
  ],
  "approvers": ["operator"],
  "sandbox": "/srv/gitpickle/recovery"
}
```

Supply `GITPICKLE_MANAGEMENT_TOKENS` through a runtime credential provider. Its JSON
object maps each random token to `{"actor": "reader", "agent": true}`.
Use at least 32 random ASCII characters per token. Each token identifies one
principal. Only a trusted human credential uses `"agent": false`. Tokens do not
appear in command arguments or policy files. The service removes the environment
variable after loading it and stores token digests in memory. Restart the service
to rotate credentials or reload the policy. Keep policy revisions increasing.

```sh
gitpickle serve --state /srv/gitpickle/archive --policy /srv/gitpickle/policy.json
```

## Management protocol

Send `POST /v1/management` with an `Authorization: Bearer` header and a JSON body:

```json
{"operation": "status", "arguments": {"repository": "github-123"}}
```

The service supports these exact argument sets:

| Operation | Arguments |
| --- | --- |
| `status` | `repository` |
| `restore_plan` | `repository`, `destination`, `expires_at`, `maximum_bytes` |
| `approve_plan` | `reference`, `digest` |
| `plan_apply` | `plan`, `approval` |
| `job_get` | `job` |
| `job_execute` | `job` |

Authentication precedes dispatch. Client fields cannot select an actor. Status
requires a current repository grant. Restore operations use the shared `Plans`
service. That service checks grants, approval bindings, expiry, and destinations
again before execution. Agent destinations stay inside the configured sandbox.
Approval requires a trusted human principal in `approvers`. See
[authorized plans](authorized-plans.md) for durable acceptance and replay rules.

The listener serializes requests. Bodies have a 64 KiB limit. Connections have a
five-second read timeout. Cross-origin browser requests are rejected. Requests,
headers, bodies, and raw exceptions are never logged. Responses contain fixed
error codes. Status returns only the selected repository and its health result.
This interface does not expose logs, shell commands, or arbitrary URL fetching.

## Untrusted archive and Git inputs

Archive reads and writes reject traversal, absolute keys, control characters,
backslashes, and symlinks. Metadata records have a 16 MiB limit. Restores reject
existing destinations and pin the parent directory with a file descriptor.
Offline recovery applies these path checks without requiring daemon credentials.
The service user owns the private archive. Concurrent hostile filesystem writers
with that same operating-system identity are outside this isolation boundary.

Git ignores inherited configuration, replacement objects, proxies, and credential
helpers. Hooks and filesystem monitors are disabled. Captures use fresh staging
without source configuration, templates, hardlinks, or checkout filters. They do
not fetch submodules. The archive adapter accepts only local clone sources.
The GitHub adapter supplies fixed HTTPS origins, disables redirects, rejects
nonpublic DNS addresses, and pins clone addresses through Git's curl settings.
LFS uses the explicit GitHub endpoint and private staging. TLS validation remains
enabled. The implementation trusts GitHub's authenticated LFS service responses;
it does not implement a general URL-fetching interface.

Git children run in separate process groups. Limits are 3,600 seconds of elapsed
and CPU time, 8 GiB of address space, 16 GiB per file, 256 descriptors, and 64 MiB
per output stream. The runner checks output growth during execution. It kills
remaining descendants on completion or failure. Limit failures return redacted
process errors. These bounds can reject large repositories; a failed capture
must never be reported as complete.

Source URL labels lose user information, query strings, and fragments before
publication. GitHub credentials enter children through a temporary environment,
not arguments or saved Git configuration. Child environments exclude management
and storage credentials. Treat existing archives as sensitive: this change does
not rewrite older immutable records that contain secrets.

## Local acceptance

`cargo xtask check` includes `tools/test_gitpickle_security.py`. The security tests
exercise real authenticated HTTP requests on free loopback ports, grant denial
and revocation, actor spoofing, malicious Git hooks and configuration, source
schemes, private DNS addresses, traversal, symlinks, process limits, and secret
redaction. Generated resources use `LOOPER_RESOURCE_PREFIX`. Fixtures and reports
stay in ignored directories. These checks do not claim an external security audit.
