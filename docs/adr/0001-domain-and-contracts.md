# Domain boundaries and application contracts

Status: accepted for T002. Source: `gitpickle.md`, sections 2–4 and Appendix A.
This record defines contracts. It does not claim that storage or daemon workflows exist.

## Decision

Use one Rust executable and one daemon. Use an embedded SQLite index and one active writer per archive.
The command-line interface (CLI), terminal user interface (TUI), and Model Context Protocol (MCP) share application services.
Adapters implement ports. The domain uses only the Rust standard library.
The application depends only on the domain. Wire serialization belongs to interfaces.
The executable connects interfaces, services, and adapters.

| Context | Owns writes and schema | Aggregate | Excludes |
| --- | --- | --- | --- |
| Protection | Source mapping, registration, policy, coverage, freshness | Protected repository | Git transport, provider types |
| Archive | Immutable dependencies, manifests, publication, verification | Recovery point | Discovery, prompts |
| Git Access | Mode, sessions, committed hosted refs, push rules | Hosted repository | Archive deletion |
| Recovery | Selection, plans, dependency resolution, validation | Recovery plan | Source availability |
| Execution and Access | Jobs, attempts, actors, grants, approvals, audit | Job | Duplicate domain policies |

Each context owns its state. Cross-context operations use application ports and IDs.
There is no shared mutable repository aggregate or aggregate for the whole account.
Execution and Access can split into modules when their ownership requires it.

`identity.rs` defines opaque IDs. An internal repository ID survives a source rename or transfer.
`SourceMapping` pairs an integration ID with an upstream repository ID. A name is only an alias.
A new upstream ID with an old name identifies a different source repository.
IDs contain 1–128 ASCII letters, digits, underscores, or hyphens. They are case-sensitive.
Adapters convert external identifiers before constructing these values. IDs are not filesystem paths.
Git SHA-1 and SHA-256 hashes retain their algorithm. Archive, plan, and LFS digests remain separate types.
Here, LFS means Git Large File Storage. Digests use lowercase hexadecimal SHA-256.
Provider ETags never enter the domain or substitute for archive content verification.

## Authority and transaction boundaries

Application implementations authorize every call, including reads and job inspection.
Each mutation has an actor, operation, explicit repository scope, and idempotency key.
Empty mutation scope is invalid. A frontend resolves an unambiguous checkout or explicit `--all` before submission.
The server checks the resolved scope against grants. It never interprets an empty scope as all repositories.
Deduplication uses actor, operation, and key. Different arguments with the same key return `conflict`.

An approval reference identifies server-held authority. It does not itself confer authority.
A plan binds its version, actor, repository IDs, destination, visibility, expected versions, digest, expiry, and impact limits.
Application services revalidate all bindings before execution. No request boolean can approve a plan.
Agent restore destinations require an approved location or managed sandbox.
Repository content, logs, and source responses never grant authority.

`AcceptedJob` means the recovery-critical request is durable outside the local database.
Closing a client detaches it. Cancellation is explicit and stops only at safe boundaries.
Cancellation cannot remove committed records. Execution is at least once, with idempotent effects.
Queue limits, per-source limits, per-repository serialization, fair scheduling, and low-disk admission belong to Execution.

Domain events describe committed facts. Emit them only after the corresponding durable record exists.
Use in-process delivery and a durable outbox when needed. No distributed broker is required.
`CaptureCommitted` and `VerificationRecorded` remain separate facts.
An upload timeout has an unknown outcome. Reconcile by operation identity and digest before retrying.
A lost response after publication cannot justify a conflicting second publication.

## Archive and hosting decisions

Prefer immutable packs with complete, traversable dependency inventories and versioned manifests.
Preserve refs, symbolic HEAD, hash algorithm, source identity, dependency sizes and digests, and required LFS identifiers.
Empty repositories can have metadata-only points. Thin packs cannot require unavailable bases.
No-change captures refresh source evidence and reuse content. They do not rewrite historical coverage.
Submodule pointers are retained; dependent repositories require separate authorization.

This direction does not freeze the archive layout. T010 selects format version 1 after a restore prototype.
SQLite indexes, pack indexes, caches, and transient progress remain rebuildable.
Durable policies contain credential references, never raw secrets.
Recovery needs archive access and configured recovery keys, not source credentials or the old daemon.

Keep application retention append-only initially. Do not add automatic deletion or garbage collection.
Provider retention controls require separate capability evidence. Application immutability is not provider immutability.
A second daemon opens an archive read-only. Writable takeover requires fencing and reconciliation.
A local process lock is not cross-server fencing. Conflicting chains stop mutation; timestamps cannot select a winner.

Hosting is off by default. A protected mirror rejects pushes.
Conversion to hosted mode retains a checkpoint and pauses source updates before accepting writes.
Resuming source updates requires explicit reconciliation. Hosting cannot delete archive history.
Readers see committed generations only. Push success requires durable object closure and publication.
LFS upload success also requires durability. Missing-LFS push policy remains a T036 decision.
No asynchronous post-receive upload can satisfy the push contract.

Prefer integrated walgit crates behind `GitEngine`, subject to T008 and T035 gates.
The current `GitEngine` is a minimal inspection and validation port, not a receive-pack implementation.
The spikes add transaction methods only after proving the response boundary.
Fallback: standard Git HTTP with a tested durability coordinator. Backup work does not wait for writable hosting.

## Interaction and deployment decisions

Use concise CLI output and an opt-in TUI. Both use the same services and authorization.
Local MCP uses standard input and output. Do not provide shell, unrestricted fetch, or raw credential tools.
Read tools are status, repository listing, recovery-point listing, and job inspection.
Backup needs an operator grant. Restore planning needs read/plan authority. Applying a plan needs scoped approval.
Pagination is bounded to 100 items. Cursors are opaque, at most 1024 bytes, and scoped to actor and filters.
Log access and source-code access use separate grants. Returned diagnostics are redacted.

Ship a binary with systemd and a version-pinned container image for Docker, Podman, and Coolify.
Management binds locally by default. Private HTTPS hosting is optional; public hosting requires explicit configuration.
Network membership does not replace per-repository authentication.
Run unprivileged. Reject unsafe paths, sanitize Git configuration, disable repository hooks, and bound child processes.
The container supports health, readiness, standard output logs, and graceful termination.
A cache volume improves speed but cannot be required for recovery.
Native Git runtime requirements must appear in packaging. Clients need ordinary Git only.

Keep GitPickle as a working name. Public branding remains subject to the separate naming task and written permission.
Use RepoPickle if the owner selects that fallback after the required review.

## Alternatives and consequences

Microservices, a broker, distributed leases, and an object-store filesystem add unsupported failure modes.
One shared repository model couples unrelated policies and makes schema ownership unclear.
Provider types in domain models prevent deterministic tests and independent archive readers.
Separate frontend policies allow authorization and status to disagree.
Mutable mirrors alone lose historical states. A provider WAL alone does not establish independent recovery points.

The selected design needs explicit conversions at boundaries and independent durable evidence.
It deliberately limits the first deployment to one writer and read-only Git hosting.
It makes no performance claim before measurement at the 404-repository workload.

## Evidence and revisit triggers

`cargo xtask check` runs dependency checks, domain rules, negotiation, serialization, and transcript checks.
Compile-fail documentation verifies that Git hashes cannot become archive object IDs.
Dependency tests inspect Cargo metadata, including build, target, and test dependencies.
Source checks add a guard for named infrastructure types. They do not replace architectural review.

Revisit deployment only for a demonstrated scale or availability requirement.
Revisit the engine after T008/T035 results. Revisit the archive after T010 measurements.
Revisit retention after its dependency and deletion models pass. Revisit mode reconciliation after a modeled use case exists.
Revisit the TUI if it duplicates policy or impairs reliability. Add remote MCP only for a concrete authenticated use case.
Revisit Rust only if a bounded prototype shows a simpler maintainable implementation.
