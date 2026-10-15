# Status and recovery evidence

Query a local archive without changing archive data or rebuilding a database:

```sh
gitpickle status --archive /path/to/archive
gitpickle status --archive /path/to/archive --repository github-123 --json
gitpickle status --archive /path/to/archive --check
gitpickle doctor --archive /path/to/archive --attention
```

Set `GITPICKLE_ARCHIVE` to use that archive with `gitpickle` and no arguments.
Inside a recognized checkout, the source remote selects its registered repository.
Use `--all` to inspect the complete archive scope. Every result prints its effective scope.
`status` shows a compact summary and repositories that need attention.
`doctor` shows scoped evidence. `doctor --attention` limits details to repositories that need attention.
Native Git and Python 3 are runtime requirements. The executable embeds the reference service.
An installed executable does not require a source checkout or the original daemon database.
These commands currently query a local archive directory. They do not contact GitHub or a remote daemon.

## Current and verified

A repository needs all of these conditions:

- A fresh successful source observation covers each required component.
- Each observation matches the latest published state.
- Git, required Git Large File Storage (LFS), and enabled wiki coverage are complete.
- Successful verification evidence names the same repository, point, and required components.
- No newer source failure or recorded loss of source access remains unresolved.

The default freshness threshold is eight hours. Exactly eight hours remains current.
Use `--freshness-seconds SECONDS` to select another positive threshold for the query.
Future source times and future verification evidence cannot establish current protection.
An unchanged capture refreshes source confirmation and capture completion without changing the retained point.
It uploads no Git or LFS payload. Replaying an old capture operation does not refresh source confirmation.
Discovery timestamps and successful job results do not establish content coverage or freshness.
A Git-only source query cannot refresh LFS or wiki confirmation.
Newly enabled wiki coverage becomes required even before the next capture.

The claim applies to the observed state. It cannot include changes that occur after the observation.
GitHub issues, pull requests, discussions, release assets, packages, and account settings remain **not included**.
These exclusions appear in every successful result. They do not count as missing required coverage.

## Scoped times

Each non-null time includes its repository, point, components, and durable evidence reference.
Component details retain separate observation and verification evidence.

| Time | Meaning |
| --- | --- |
| `source_observation` | Successful content confirmation; distinct from discovery of repository names |
| `content_change` | First publication of the most recently observed changed content |
| `capture_completion` | A completed capture coordination step, including an unchanged capture |
| `verification` | Latest successful integrity check for the stated point and components |
| `restore_test` | Latest successful sampled restore for the stated repository and point |

A repair of missing LFS coverage alone does not advance the content-change time.
Publication order selects the latest point. Timestamps never resolve publication conflicts.
Old archives without publication times report an unknown content-change time.
They remain readable. Missing evidence remains unknown rather than receiving an invented time.
A verification time can cover only some components. Read the component scopes before interpreting that time.
`last_verified_point` shows what remains recoverable when current protection degrades.

The reference service exposes `gitpickle_status.restore_test(writer, repository, point)`.
The caller must hold an authorized writer session. The function reconstructs archive bytes in clean staging.
It records a successful `independent_restore` check only when all required components pass.
Capture readback remains integrity evidence and never becomes a sampled restore result.
Status and doctor themselves perform no restore tests and write no evidence.

Restore results retain their historical point and component scopes.
`restore_tested_repository_ids` lists only repositories with successful sampled evidence.
A historical restore test does not prove that a newer point was tested.
A single sampled repository never becomes a claim that the entire portfolio passed.

## Results and next actions

JSON uses the version 1 result envelope from [the product contract](product/contracts.md).
A successful query always reports `outcome: ok`, including degraded protection.
The headline derives from coverage and freshness, not a successful job exit code.
Every gap has a stable code, repository ID, and component.
Each affected repository reports one structured next action and its retained verified point.

| Command or failure | Exit |
| --- | --- |
| Successful `status` query, including degraded protection | 0 |
| Healthy `status --check` or `doctor` | 0 |
| Degraded `status --check` or `doctor`, including an empty archive | 6 |
| `invalid_input`, `configuration` | 2 |
| `forbidden` archive access | 3 |
| `conflict` publication or protection history | 4 |
| `not_found`, `integrity_failure`, `storage_unavailable`, `operational_failure` | 5 |
| `unsupported_version` | 7 |

Recorded source authentication failures are protection gaps. They do not mean the status query failed.
A query that can read such evidence returns 0; its health check returns 6.
Query failures use `outcome: failed` and a stable error, without raw paths, credentials, or provider diagnostics.

## Local acceptance

Run the status suite:

```sh
python3 -B -m unittest discover -s tools -p test_gitpickle_status.py
cargo xtask check
```

The tests use real local Git captures, missing LFS data, wiki requirements, and archive-only sampled recovery.
They cover stale sources, unchanged captures, source failures, historical evidence, and 404 registered repositories.
They also check excluded metadata, read-only queries, scope selection, stable exits, and installed executable behavior.
Generated archives use `LOOPER_RESOURCE_PREFIX` under ignored `fixtures/generated/status/`.
No external resources or credentials are required. These checks make no cloud or portfolio performance claim.
