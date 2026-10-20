# Authorized execution plans

`tools/gitpickle_plans.py` provides the local application service for authorized restores.
It uses the existing archive reader and single-writer coordinator.
TLA+ (Temporal Logic of Actions) safety checks preceded its implementation.
See [the model contract](../spec/README.md#authorized-execution-plans) for assumptions and fault mappings.

## Trusted inputs

The server constructs `Principal` from authenticated identity and transport context.
An agent cannot supply its actor, its channel, grants, or policy configuration through tool arguments.
`PolicyStore` comes from trusted configuration. Its grants contain actor, permission, repository IDs, and expiry.
An optional `sources: ["github"]` grant also covers repository IDs of the form `github-123`.
It does not cover local repositories, wiki IDs, other actors, or other permissions.
The grant has the same expiry and revocation checks as an exact repository grant.
Only the trusted policy can supply source grants. A protection preview cannot create one.
`plan_restore` and `apply_plan` are separate permissions. Both require the entire repository scope.
The approver must appear in the current policy and use a trusted session.
An agent session cannot approve a plan, even when its actor appears in the approver list.
A preauthorized policy adapter can use the same approval method through a trusted server principal.
Repository text, source labels, and Git refs never grant authority.

The policy loader must persist monotonically increasing revisions across restart.
`PolicyStore.replace` rejects a revision that does not increase.
Policy replacement and execution use the same lock.
Transport authentication and configuration persistence belong to the daemon integration in T019.
The local Model Context Protocol (MCP) transport in T024 must expose only the constrained service methods.
Never expose `approve`, `PolicyStore`, or the raw recovery adapter as agent tools.
The offline recovery adapter remains available to an operator with archive access.

## Scheduled capture authority

Applying protection stores the setup reference in the durable protection record before scheduling jobs.
Scheduled captures use that applied plan's actor and recheck the current capture grant before source access.
Other operators' previews do not change that actor. Applying another plan explicitly transfers authority.
Plans that include future repositories require live GitHub source grants for `protect` and `backup`.

Legacy protection records without a setup reference use completed setup results to identify one applied actor.
Unused previews never supply authority. If legacy results identify different actors, prepare and apply a new protection plan.
That new plan records an explicit actor. A lost apply response preserves the durable setup reference.

## Review and execution

1. Call `restore_plan` with the authenticated principal, repository ID, destination, expiry, and impact limits.
2. Show the returned plan and its digest in a trusted session.
3. Call `approve` with the stored plan reference and the reviewed digest.
4. Call `plan_apply` with the exact returned plan and the server-held approval reference.
5. Execute the returned job ID through the server, then inspect its result.

The plan contains its schema version, unique identity, actor, channel, archive identity, operation, and repository IDs.
It also contains the exact destination, visibility, parent identity, expected state versions, expiry, impact limits, and recovery selection.
The digest covers every field except the digest itself using canonical JSON (JavaScript Object Notation).
Application compares canonical candidate bytes with the immutable stored plan and approval snapshot.
A recomputed digest cannot authorize changed fields. `approved: true` cannot reference an approval.

Expected versions include the policy revision and each repository's publication head.
The service checks grants, versions, digest, expiry, component closure, and impact limits again before execution.
Repository and policy locks prevent local changes between final validation and the restore.
Impact limits bound repository count and archive input bytes, including required LFS (Large File Storage) and wiki data.
They do not promise a bound on expanded filesystem size or Git process memory.
Only complete, verified default selections and private restores into new directories are currently supported.
Unsupported visibility, overwrite, and other operations fail closed.

Acceptance is durable before the service returns a job ID.
Every plan has one stable job identity, including when an operator creates another approval for the same plan.
A second application returns `replayed_plan`. A second execution also returns `replayed_plan`.
Clients can inspect an accepted job after losing its reply.
Restore jobs do not enter the capture scheduler. The server calls `execute` independently of the requesting client.
The service can share the active `Jobs.writer`; it does not acquire a second archive lock in that case.

## Destinations and audit records

Agent restores use a direct child of the managed sandbox or an exact destination in the operator policy.
The managed sandbox must exist, belong to the service user, and reject group or public writes.
The service rejects existing destinations, traversal, symbolic links, and changed parent identities.
It walks parents with `openat` and `O_NOFOLLOW`, then pins the parent through installation.
Native Git reads the descriptor through `/proc/<server-pid>/fd/` on Linux.
A concurrent parent rename cannot redirect the restore into a replacement directory.
Operator-approved external parents require the same private ownership checks for agent sessions.

Audit records contain actor, repository scope, operation, policy revision, result, plan digest, and time.
They exclude destination paths, source labels, credentials, Git text, and exception messages.
Plan responses also exclude source labels and Git text.
Internal plan records contain the destination because recovery needs it. Keep the archive private.

The service records consumption durably before filesystem effects.
If the process stops after consumption, `inspect` returns `unknown_outcome` until a terminal record exists.
Execution cannot reuse that approval. Inspect the destination before preparing a new plan.
This behavior includes a crash after successful restore but before the terminal record.
No automatic retry can repeat an effect whose completion is uncertain.

## Local checks

```sh
python3 -B -m unittest discover -s tools -p test_gitpickle_plans.py -v
cargo xtask model --suite pr
cargo xtask model --suite regression
cargo xtask check
```

Tests use private paths under ignored `fixtures/generated/plans/` with `LOOPER_RESOURCE_PREFIX`.
They create no external resources, credentials, servers, or provider objects.
Evidence covers the local archive adapter. Provider and daemon validation remain in their existing tasks.
