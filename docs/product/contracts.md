# User-facing contracts

Status: contract version 1, defined by T002.
The executable supports help, version, local archive status, doctor, and storage-only recovery.
See [status and recovery evidence](../status.md) for the implemented evidence calculation.
Other workflows remain acceptance targets for later tasks.
The contract tests exercise types, rules, wire envelopes, and fixtures, not live backup or recovery.

## Commands and scope

| Command | Meaning |
| --- | --- |
| `gitpickle` or `status` | Query protection; show the effective repository or server scope |
| `protect github` | Preview and apply ongoing protection for confirmed authorized repositories |
| `protect` in a checkout | Preview protection for the detected remote; preserve local work and remotes |
| `backup` | Capture source content now |
| `sync` | Refresh source-tracking state; never perform implicit two-way synchronization |
| `restore` | Preview recovery to a new directory from the newest complete verified point |
| `restore --from-storage` | Reconstruct inventory and recover without the old server or GitHub |
| `doctor` | Inspect configuration, gaps, and recovery evidence |
| `serve` | Run the daemon |
| `tui` | Open the optional terminal interface |
| `mcp` | Run the constrained local Model Context Protocol server |
| `help`, `help --all` | Show the normal path, then advanced controls |

Writes require explicit repository scope, an unambiguous checkout, or `--all`.
Changing directories cannot expand mutation scope. All interfaces use the same application services.
Never prompt without terminal input. Report missing inputs as `invalid_input`.
Honor `NO_COLOR`, use status words, support keyboard input and narrow terminals, and keep plain output available.
Human timestamps include a time zone. Resolve relative dates to exact timestamps before approval.

Setup resumes completed steps without repeated authorization, duplicate jobs, or duplicate buckets.
Preview execution location, source scope, storage, runtime dependencies, privilege changes, and impact before applying.
Probe authentication, staging space, storage access, and required capabilities in an isolated prefix.
Bucket creation needs separate permission. Secrets use a credential provider or masked input, never command arguments.
Future authorized repositories require explicit scope confirmation. Say “authorized repositories found.”
A missing source is “source unavailable”; it is not proof of deletion.

Defaults: capture every six hours, discover daily, and warn after eight hours without source confirmation.
Keep all retained points. Hosting and vendor telemetry default to off. Restore defaults to a new directory.
Enabled wikis are required components. Missing required LFS or wiki content prevents complete verification.
Unpushed commits, dirty files, inaccessible refs, and never-captured data are outside the claim.
Issues, pull requests, discussions, release assets, packages, and account settings are not included.

## Results and errors

`--json` writes one JSON result and a newline to stdout. Progress and diagnostics go to stderr.
MCP uses protocol framing and never mixes terminal prose into stdout.
The wire contract is `interfaces::machine::ResultV1<T>`. Operation data is typed by the operation's response contract.
The current fixtures use JSON objects for operation data while later services define their full wire mappings.

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1`; reject unsupported versions before interpreting data |
| `observed_at_unix_seconds` | Unsigned seconds since the Unix epoch in UTC |
| `scope` | Explicit `repository_ids` and nullable `archive_id`; IDs are stable, names are aliases |
| `outcome` | `ok`, `queued`, `running`, `committed`, `verified`, or `failed` |
| `job_id` | Durable accepted job ID; required for queued and running results |
| `coverage_gaps` | Repository ID, component, and stable code for every reported gap |
| `evidence_ids` | References to scoped, timestamped evidence; never a global health assertion |
| `next_action` | One structured operation and argument object, or null |
| `data` | Operation response, or null; null on failure |
| `error` | Stable code, redacted message, and explicit retry advice; present only on failure |

`ok` means the query or preview succeeded. `queued` does not mean a backup exists.
`committed` means durable publication. `verified` requires independent evidence for the stated scope and no coverage gaps.
Retry advice is `never`, `after_seconds` with seconds, or `reconcile` with a stable operation ID.
An unknown write outcome requires reconciliation, not a blind new mutation.
Consumers ignore unknown additive fields. Removing fields or changing meanings requires a new schema version.
Consumers reject unknown outcome values. Unknown error codes remain failures; never infer success from English text.
No credentials, sensitive source URLs, provider errors, stack traces, or repository text enter error messages unredacted.

| Exit | Stable errors or outcome |
| --- | --- |
| 0 | Successful query, preview, or durable job acceptance |
| 2 | `invalid_input`, `configuration` |
| 3 | `authentication`, `forbidden`, `approval_required` |
| 4 | `conflict`, `read_only`, `unsafe_destination` |
| 5 | `source_unavailable`, `rate_limited`, `storage_unavailable`, `integrity_failure`, `low_disk`, `unknown_outcome`, `not_found`, `operational_failure` |
| 6 | `incomplete_coverage`, `stale_coverage`, or a degraded `status --check` |
| 7 | `unsupported_version`, `unsupported_capability` |

`status` returns 0 when its query succeeds, even when protection is degraded.
`status --check` returns 6 for missing or stale required coverage. Query errors retain their error exit code.
Ordinary Git clients use Git protocol errors and their own exit codes, not this management exit table.

## Negotiation

Before dispatch, exchange software version, management version range, machine-result version range, and advertised capabilities.
Version ranges are inclusive positive integers. Initially both protocols support `[1, 1]`.
Choose the highest common version independently for management and results.
Reject malformed ranges with `invalid_input`. Reject disjoint ranges with `unsupported_version` before accepting work.
Package versions are diagnostic and need not match. Archive format compatibility is a separate reader check.
Capabilities describe available operations, such as `status`, `backup_run`, `restore_plan`, and `hosted_push`.
Reject a required missing capability with `unsupported_capability`. Never silently downgrade a requested operation.
An unimplemented operation must not be advertised. A new capability is not permission to invoke it.
Authentication and authorization remain required regardless of negotiation.
Tests cover mismatched package versions, highest-common selection, absent capabilities, and incompatible ranges.

## Evidence and recovery

Keep source observation, content change, capture completion, verification, and restore-test times separate.
Current-and-verified status requires fresh authorized observation and matching verified coverage for every required component.
An old point can be current after an unchanged source check. A successful job alone cannot establish freshness.
Restore evidence names the tested repository, point, components, method, result, and time.
One sampled restore cannot establish portfolio-wide restore success.

Default selection excludes incomplete or unverified points. Explicit incomplete selection requires a warning and missing-data preview.
Restore previews name the destination, visibility, overwrite behavior, exact point, and required authority.
Validate archive digests, dependencies, refs, HEAD, Git integrity, and required LFS in clean staging.
Report exclusions and integrity results. Bundles state whether they are full or require prerequisites.
A recovery card contains archive identity, provider, bucket/prefix, format version, software retrieval, and key-location guidance.
Keep keys and offline software separately protected. Source automation may need renewed credentials after recovery.

## Jobs and hosting

`--wait` follows an accepted job. Interrupting the client detaches it; server work continues.
A separate cancellation request stops at a safe boundary. It cannot undo a committed point.
Paginated lists return at most 100 items and a nullable continuation cursor.
Job inspection resumes by stable ID. Default status summarizes gaps without one call per repository.

Display hosting mode and the canonical authenticated remote URL separately from protection coverage.
Protected mirrors allow advertised clone/fetch support and reject pushes.
Hosted mode requires explicit creation or conversion, a retained checkpoint, and paused source updates.
A successful push must survive loss of the original server. Storage failure cannot yield local-only success.
Advertise protocol versions, hash formats, shallow clones, filters, signatures, LFS locking, and hooks only after compatibility tests.
Git SSH transport and GitHub platform features remain separate from Git HTTPS support.
