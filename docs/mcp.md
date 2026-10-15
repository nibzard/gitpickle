# Local agent tools

Run `gitpickle mcp --profile local` as a local Model Context Protocol (MCP)
server. The executable embeds the service. Python 3 and native Git remain runtime
requirements. The daemon continues accepted work after the client disconnects.
There is no remote MCP listener.

The stdio interface uses newline-delimited JSON-RPC 2.0. It implements MCP
version `2025-11-25`, initialization, ping, tool discovery, and tool calls.
See the [transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [tool specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
Protocol output contains no terminal prose. Diagnostics use standard error.

## Select a profile

Store this nonsecret profile in `~/.config/gitpickle/profiles.json`.
Alternatively, set `GITPICKLE_PROFILES` to its path.

```json
{
  "profiles": {
    "local": {
      "server": "http://127.0.0.1:8080",
      "token_env": "GITPICKLE_AGENT_TOKEN"
    }
  }
}
```

Supply the token through the named runtime environment variable. Do not put it
in the profile or client configuration. Configure that token in the daemon with
an agent identity. Use a dedicated actor and repository-scoped grants. Read
[security boundaries](security.md) for the policy format and token setup.

The application treats every call on this surface as an agent call. A human
credential cannot bypass the agent destination rules through this surface.
The daemon supports the same operations through its authenticated management
interface, using the `agent_` prefix. Authorization runs in that application
service, independently of MCP annotations or client validation.

## Tools and authority

| Tool | Required grant | Behavior |
| --- | --- | --- |
| `protection_status` | `status` | Summarize all authorized repositories; page evidence details |
| `repositories_list` | `status` | Page stable repository IDs; filter by `repository` or `attention` |
| `recovery_points_list` | `plan_restore` | Page completeness and verification for one repository |
| `jobs_get` | `status` for backups; `plan_restore` for restores | Inspect a stable job ID with `kind: backup` or `kind: restore` |
| `backup_run` | `backup` | Accept or reuse one protected repository's backup using `key` |
| `restore_plan` | `plan_restore` | Preview a new private destination in the managed sandbox |
| `plan_apply` | `plan_restore`, `apply_plan`, and trusted approval | Apply the exact stored plan by `reference` and `approval` |

Enable the daemon workflow with `serve --staging-bytes BYTES` for backups and
asynchronous restores. Backup acceptance uses the durable job service. Reusing
an actor's key with different arguments returns a conflict. Workers check grants
again before source access. Acceptance never means that a capture is verified.
Completed captures report `committed` until matching complete verification exists.

Restore plans bind the actor, repository, point, destination, policy revision,
expiry, and impact limits. A trusted operator approves the returned plan reference
and digest through the existing `approve_plan` management operation. Approval is
absent from the MCP tool set. Supply the resulting approval reference to
`plan_apply`. Application checks reject invented approvals, changed plans, stale
versions, unsafe destinations, expired grants, and replayed plans. See
[authorized plans](authorized-plans.md). Restore inspection can report
`unknown_outcome` after a process interruption; it never assumes completion.

## Bounded results

Requests and protocol responses have a 64 KiB limit. Oversized requests close
the stdio session after a protocol error. Lists default to 20 rows and allow up
to 50. Pages can contain fewer rows to keep responses within the byte limit.
Use `data.next_cursor` with the same arguments to continue. Cursors bind the
query, actor, policy revision, and result snapshot. Changed snapshots require a
new first page and return `cursor_stale`.

Results include the observation time, archive scope, repository scope, stable
IDs, coverage gaps, evidence references, and structured next actions. Times use
Unix seconds or exact UTC strings. The scope lists repositories in the current
page. `data.scope_selection` describes the full query; summary counts cover that
query. Summary gap counts cover every authorized repository, including later pages.

Ask `protection_status` for what needs attention and what lacks a retained verified
point. One optional call with `repository` supplies its detailed component evidence.
These two calls answer coverage questions without inspecting every repository.
GitHub metadata exclusions appear explicitly. Readback verification remains
separate from independent restore tests.

Source labels, repository text, refs, raw logs, and exception messages are omitted.
Treat all archive content as untrusted data. Tool inputs cannot select an actor,
execute commands, fetch arbitrary URLs, delete retention data, or approve plans.

## Preview client configuration

Run this command against an existing client configuration:

```sh
gitpickle mcp --profile local --config-preview client.json
```

It prints a merged JSON preview. Review and save that result with your client.
Existing settings and other servers remain intact. An existing `gitpickle` entry
causes a conflict; the helper never replaces it. The new entry contains only the
executable and profile arguments. It contains no storage credentials or tokens.

## Local verification

Run `python3 -B -m unittest discover -s tools -p test_gitpickle_mcp.py` or
`cargo xtask check`. Tests use authenticated requests on free loopback ports,
local Git fixtures, and the installed executable's stdio protocol. Generated
fixtures use `LOOPER_RESOURCE_PREFIX` in ignored directories. No external
provider resources or paid model evaluations are required.
