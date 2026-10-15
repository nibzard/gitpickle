# Archive format version 1

Version 1 uses immutable, non-thin Git packs and complete dependency inventories.
The reference implementation is `tools/gitpickle_archive.py`.
The executable embeds this reader for recovery. Capture remains a local reference coordinator.
The Rust storage adapters provide read-only provider access for recovery.
The reader needs Python 3, native Git, and archive read access. It needs no Python packages.
It does not need GitHub, the original repository, SQLite, or the original daemon.

## Records and identities

All records use UTF-8 JSON objects with integer `version: 1` and a `kind` string.
Reject unsupported versions and kinds. Never execute serialized content.
Writers encode sorted keys, compact separators, and ASCII escapes, without a trailing newline.
Readers hash the stored bytes before parsing. They do not reserialize records for verification.
All digests use SHA-256, lowercase hexadecimal, even for SHA-1 Git repositories.
Git object identifiers and provider ETags are separate concepts.

`archive.json` contains `kind: archive`, `archive_id`, and `encryption: none`.
The archive ID is an operator-assigned stable identifier. Use a random UUID for a new installation.
This root record is immutable. Save its digest and archive ID on the recovery card.
A digest detects corruption. It does not authenticate a malicious replacement of the entire archive.
Version 1 defines no client encryption. It requires no recovery key beyond storage credentials.
Reject unknown encryption settings. Provider encryption keys remain a provider access prerequisite.

Repository IDs are stable opaque identifiers matching `[A-Za-z0-9_-]+`.
Renames do not change this ID. A wiki uses `<id>-wiki` and the same records.
Do not infer repository identity from its current GitHub name.

Except for `archive.json` and local coordination files, each record key ends in the SHA-256 digest of its exact bytes.
All references are relative keys under the selected archive prefix.
Reject absolute keys, empty components, `.` and `..`, and symlinks.
Do not trust archive paths as local destination paths.

| Key | Kind and required fields |
| --- | --- |
| `repos/<id>/identity/<digest>` | `identity`: `archive_id`, `repository_id`, `source` |
| `repos/<id>/packs/<digest>` | Raw non-thin Git pack bytes; no JSON wrapper |
| `repos/<id>/lfs/<oid>` | Raw Git Large File Storage (LFS) content; SHA-256 OID |
| `repos/<id>/points/<digest>` | `point`: fields below |
| `repos/<id>/commits/<digest>` | `publication`: `archive_id`, `repository_id`, `point`, `parent` |
| `repos/<id>/checks/<digest>` | `evidence`: fields below |
| `ownership/<digest>` | `writer`: `archive_id`, `server_id`, `parent`, `fencing`, `heads` |
| `control/<digest>` | `control`: fields below |
| `jobs/<job-id>/events/<digest>` | `job`: append-only scheduling records; see [durable jobs](../jobs.md) |

`source` is a nonsecret stable source descriptor, for example `github:repository:1234`.
Versioned identity records preserve previous mappings. Never place credentials in a source descriptor.

A point has these required fields:

- `identity`: the identity record key.
- `hash_algorithm`: `sha1` or `sha256`.
- `refs`: a map from full Git ref names to Git object IDs, including annotated tag IDs.
- `head`: exactly `{"symbolic":"refs/heads/main"}` or `{"detached":"<git-oid>"}`.
- `packs`: the complete list of `{key, size}` objects, including all reused packs.
- `objects`: the cumulative Git object ID inventory used by this prototype for pack reuse.
- `lfs`: a map from required SHA-256 OIDs to `{key, size}` objects.
- `coverage`: `git` and `lfs`, plus `wiki` when enabled. Each component is `complete` or `incomplete`.
- `wiki` (optional): an immutable point key under `repos/<id>-wiki/points/`, or `false` when unavailable.
- `parent_publication`: the preceding publication key, or `null` for the first point.

Sizes are nonnegative byte counts. Ref names must pass `git check-ref-format` and begin with `refs/`.
Symbolic HEAD can name an unborn branch. Empty repositories have no packs and an empty ref map.
Do not create an empty Git bundle. Detached HEAD content belongs in the object closure.
LFS requirements include pointers in all reachable historical commits, not just the current checkout.
The writer records every required LFS OID and size, including content unavailable from the source.
Missing content, corrupt source bytes, and failed transfers produce `lfs: incomplete`.
Git packs remain recoverable. Corrupt retained archive bytes stop capture and recovery.
A later capture can publish complete coverage without uploading Git packs again.
The earlier point retains its incomplete coverage even after its missing objects become available.
Unsupported pointer syntax and conflicting sizes stop publication. Extended LFS pointers remain unsupported.

Enabled wikis use separate Git points, packs, LFS objects, and verification records.
The main point links the exact wiki point and records aggregate wiki coverage.
Disabled wikis have no required wiki component. An enabled but unavailable wiki records `wiki: incomplete`.
A wiki point cannot link another wiki. Its identity must match `<id>-wiki`.
A complete wiki point includes complete Git and required LFS coverage.
Recovery validates linked wiki digests and restores it into `wiki.git` inside the main bare repository.
You can also restore the wiki point independently to a new directory.
No source access or child publication chain is needed to restore a linked wiki point.
The result reports `verified_components` and the separate wiki integrity result.
Incomplete coverage requires explicit point selection and `--allow-incomplete`.
Only successfully checked components receive verification evidence.
Submodule Git pointers remain Git data. Capture and recovery never fetch their dependent URLs.

Each pack is independently indexable without an external delta base.
Packs can contain commits whose parents occur in another listed pack.
Therefore, validate the complete Git graph after installing every listed pack.
Do not infer dependencies from a daemon cache, a previous point, or a pack index.
Compaction publishes a new point; it never replaces old packs or records.
Extra historical objects in reused packs do not add refs to the restored repository.

## Publication and auxiliary records

Use one externally fenced writer and serialize each repository.
Write payloads, identity, and point before the publication record.
Read back and validate dependencies before reporting durable completion in a production adapter.
A filesystem write in this prototype is not a provider durability claim.
The local coordinator implements isolated capture and ambiguous-response reconciliation.
Unpublished objects are ignored by inventory and retained as safe leaks.
No garbage collector or automatic retention deletion exists.

List every page under `repos/<id>/commits/` to rebuild the catalog.
Verify every digest, version, archive ID, repository ID, and referenced point.
The publication parent must equal the point's `parent_publication`.
Exactly one publication can have a null parent. Each predecessor has at most one successor.
Reject missing predecessors, forks, cycles, and conflicting identities. Never select a chain by timestamp.
A directly selected point restores without traversing that chain or any preceding point.
A damaged chain prevents automatic inventory selection, but does not erase independently readable points.

An unchanged capture reuses the existing point and uploads no Git or LFS payload.
The coordinator appends a separate source observation, rather than another identical publication.

An `evidence` record contains `archive_id`, `repository_id`, `point`, `recorded_at`,
`method`, `components`, `passed`, `reader_version`, and `details`.
`recorded_at` is an RFC 3339 UTC timestamp.
Methods are `archive_readback`, `git_integrity`, `lfs_digest`, and `independent_restore`.
Components are `git`, `lfs`, or `wiki`. Evidence applies only to its selected point and components.
Upload success alone is not restore evidence. Restore returns a result without requiring archive write access.
An authorized coordinator can append this result as evidence.

A `control` record contains `archive_id`, `operation_id`, `record_type`, `recorded_at`,
`predecessor`, and `payload`. `predecessor` is a prior control key or null.
Record types are `policy`, `job_accepted`, `job_terminal`, `capture_intent`, `source_observation`, and `audit`.
The payload is a versioned application contract; readers must not run commands from it.
Accepted requests preserve the scoped operation ID and request digest.
Terminal records preserve the outcome and accepted-record key. Conflicting terminal outcomes are errors.
Policy records exclude credentials. Source automation can require renewed authorization after recovery.
These auxiliary records do not alter a point's immutable coverage or its Git dependencies.
The job service implements durable replay with separate chained `job` records.
See [durable jobs](../jobs.md) for their fields and reconstruction rules.

Capture intents bind an operation ID to a repository ID and a nonsecret source descriptor.
Their payload contains `repository_id` and `source`.
Source observations contain `repository_id` and `point` in their payload.
Their timestamp records completion of source capture, before archive publication and verification.
New publications include the capture's `operation_id`. Older publications remain readable.
Use `capture --operation-id ID` to repeat one capture after a lost response.
The coordinator rejects reuse with a different source descriptor.
Replay finds the original publication or observation from archive records, without the source or local projection.
It checks archive bytes again. It does not create a new source observation.
A new capture receives a new operation ID unless you supply one.
The job service uses these capture records to reconcile committed effects before retry or cancellation.

## Independent recovery procedure

1. Obtain the archive prefix, archive ID, root digest, and storage access from your recovery card.
2. Verify `archive.json`. List publications, validate the chain, and select a point.
3. Validate the point and identity digests. Fetch every listed pack and required LFS object.
4. Check each byte count and SHA-256 digest. Reject missing or corrupt dependencies.
5. Initialize a new bare repository with the recorded hash algorithm. Run `git index-pack --stdin` for each pack.
6. Recreate refs with `git update-ref`. Restore symbolic HEAD with `git symbolic-ref`, or detached HEAD with `update-ref --no-deref`.
7. Run `git fsck --full --strict`. Store verified LFS bytes under `lfs/objects/<first2>/<next2>/<oid>` and report the exact scope.

Never overwrite an existing destination. Validate in private, clean staging before creating the destination.
A failed validation removes staging and leaves the destination absent.
An interrupted process can leave staging, which is never treated as a recovered repository.
Run untrusted archive recovery with operating-system resource limits and a current Git installation.
The reference reader sanitizes inherited Git configuration and disables hooks.
It loads pack payloads into memory; it is not suitable for the 10 GiB release workload.

```sh
python3 tools/gitpickle_archive.py init fixtures/generated/archive example-archive
python3 tools/gitpickle_archive.py capture fixtures/generated/archive /path/to/source.git repo123 github:repository:123
python3 tools/gitpickle_archive.py restore fixtures/generated/archive repos/repo123/points/DIGEST /new/recovered.git
python3 -m unittest discover -s tools -p test_gitpickle_archive.py
python3 tools/gitpickle_archive_compare.py > perf/results/archive-comparison.json
```

Create `perf/results` before redirecting the comparison output.
See [the measured decision](tradeoffs.md) for the representation choice and experiment limits.

## Writer ownership

The local reference capture now uses `tools/gitpickle_writer.py`.
Keep a `Writer` session open for the lifetime of a coordinator.
Its operating-system file lock rejects competing local processes.
Never delete `.writer.lock`, even after a crash. The kernel releases the lock when the process exits.
The file is local coordination metadata, not a portable archive record.
Per-repository locks cover inspection, staging, and publication. Different repositories can progress concurrently.

Ownership records use `ownership/<digest>` and `kind: writer`.
Required fields are `archive_id`, `server_id`, `parent`, `fencing`, and `heads`.
`parent` links the previous ownership record, or is null for the first owner.
`heads` maps repository IDs to reconciled publication keys, or null for an empty repository.
`fencing` is null for initial ownership, otherwise a nonsecret operator attestation of external fencing.
Ownership records are immutable. Reject forks, missing predecessors, and cycles.
New publications include `writer`, the ownership record key. Legacy publications remain readable.

The default server identity hashes `/etc/machine-id`.
Set `GITPICKLE_SERVER_ID` to a persistent, unique installation ID when machines share or clone that identifier.
Keep this ID across restarts. Never copy it to a second active server.
An empty archive assigns its initial owner. A foreign server remains read-only.
A legacy archive with publications requires explicit adoption through the takeover procedure.
Restore does not acquire a writer lock or modify ownership.

Before takeover, stop and isolate the previous writer. Revoke its archive write credentials or restrict its access.
A local file lock does not fence another server. This implementation does not verify provider credential revocation.
The operator supplies evidence of that external action. Do not use a timeout as fencing evidence.

1. Set the new server's persistent `GITPICKLE_SERVER_ID`.
2. Run `python3 tools/gitpickle_archive.py ownership ARCHIVE` and retain the owner key and heads map.
3. Fence the previous server outside GitPickle. Record the nonsecret evidence.
4. Save the inspected heads map as a JSON file.
5. Run `python3 tools/gitpickle_archive.py takeover ARCHIVE --previous-owner KEY --heads-file FILE --fencing-evidence RECORD`.

Use `none` as the previous owner only for an archive without ownership records.
Takeover revalidates every publication chain and matches the exact owner and heads.
It rejects missing fencing evidence, stale heads, stale owners, and active mutations.
Conflicting chains stop the session's mutations. A timestamp cannot resolve a conflict.
Repair requires an explicit operator decision and a new session; the session never resumes itself.
Directly selected points remain recoverable when a chain conflicts.

Local writes stage bytes, flush them, publish with an exclusive hard link, and flush directories.
Interrupted staging files are safe leaks under `.staging-*`; inventory ignores them.
These filesystem operations do not establish R2, S3, or B2 durability or cross-server exclusion.
Provider integration must retain this coordinator boundary.

Run the ownership acceptance tests with:

```sh
python3 -m unittest discover -s tools -p test_gitpickle_writer.py
```

## Capture publication

The local coordinator clones through native Git into private staging with `--no-local`.
It copies no source configuration, hardlinks, or alternates. It validates staging with `git fsck --full --strict`.
Ref names, object IDs, and HEAD describe the staging repository, not a later source view.
It writes immutable packs and required LFS content before the manifest.
Before publication, it restores the complete manifest from archive bytes into another empty repository.
A component declared complete must have every required dependency.
Any corrupt archive dependency stops publication.

Each immutable write uses its exact key and bytes to reconcile a lost response.
A conflicting digest or unreadable outcome stops capture. A retry discovers an existing publication through inventory.
Local flush errors stop capture even when the written bytes remain visible.
Capture flushes reused ownership and publication records again before acknowledging a replay.
This covers process exit between a local hard link and its directory flush.
The same captured state reuses that point. It does not append another publication or upload Git or LFS payloads.
Unpublished dependencies remain safe leaks. Archive deletion stays disabled.

After publication, capture appends a timestamped `source_observation` control record.
It rebuilds `.projection/<repository-id>` from the publication chain.
This local cache is disposable and excluded from archive records.
Capture then restores from archive bytes and appends separate `archive_readback` evidence.
A crash after publication can leave a committed point without verification evidence.
An error during verification does not remove the publication or imply a successful check.
A source observation reports captured state; it does not claim an independent restore test.

Run `python3 -B -m unittest discover -s tools -p test_gitpickle_capture.py`.
The tests interrupt every coordinator boundary, lose immutable write responses, and replay the incomplete-closure model regression.
They also remove the source after staging and rebuild projections after removing local state.
The archive and writer suites cover empty repositories, detached HEAD, hash algorithms, force-pushes, and deleted refs.
All tests create ignored local resources with the Looper resource prefix.
The capture tests also exit a subprocess without cleanup at publication boundaries.

This implementation uses local filesystem durability. It does not establish cloud provider durability.
The Rust storage contract remains the integration boundary for future provider-backed capture.
The reference coordinator loads packs into memory and does not establish portfolio performance.

Run a small capture benchmark from the repository root:

```sh
mkdir -p perf/results
python3 -B tools/gitpickle_capture_bench.py --repetitions 30 > perf/results/capture.json
```

The report retains raw timings, immutable write counts, read counts, and uploaded byte counts.
It measures initial capture, no-change capture, an incremental commit, and archive-only restore.
Each no-change sample must upload zero Git or LFS payload bytes and reuse the original point.
Each restore removes the source first, checks every ref, and runs the reader's integrity checks.
Counts describe local archive API calls. They exclude filesystem operations inside Git and the local projection.
These tiny-fixture results provide local correctness and cost evidence, without a cloud or release performance claim.

## Storage-only selection and exports

See [storage-only recovery](../recovery.md) for inventory, preview, explicit selection, and bundle commands.
The default selection requires complete coverage and matching verification evidence for every required component.
Publication order defines newest. Timestamps never resolve publication forks.
Missing dependencies make a point incomplete. Corrupt dependencies stop recovery.
Explicit incomplete recovery can omit missing LFS, but it must still pass Git integrity checks.
Full bundles include all Git prerequisites. The export metadata preserves refs and HEAD and labels LFS exclusion.

## Authorized plan records

The local execution service adds an optional `execution/` namespace.
Existing archive readers can recover Git data without reading this namespace.
Every immutable execution record has `kind: execution`, `version: 1`, and `archive_id`.
Records use the existing canonical envelope encoding, content digest, write flush, and readback rules.
See [authorized plans](../authorized-plans.md) for approval bindings and application behavior.

| Key | Record fields |
| --- | --- |
| `execution/plans/<digest>` | `plan`: immutable canonical plan with its independent plan digest |
| `execution/approvals/<digest>` | `plan`, stored plan `reference`, `approver`, `approved_at` |
| `execution/jobs/<job-id>/accepted/<digest>` | `actor`, `plan`, `approval`, `accepted_at` |
| `execution/jobs/<job-id>/started/<digest>` | `actor`, `started_at`: durable approval consumption |
| `execution/jobs/<job-id>/terminal/<digest>` | `result`: `completed` or `restore_failed` |
| `execution/audit/<digest>` | Redacted actor, scope, operation, policy revision, result, plan digest, timestamp, event ID |

Each job has at most one record of each event type. Conflicting event records stop execution.
The job ID hashes the immutable plan ID. Another approval cannot apply the same plan again.
The service reconstructs acceptance and consumption directly from these records after restart.
A consumed job without a terminal record reports `unknown_outcome` and cannot execute again.
These records belong to the private archive; only redacted audit fields can enter diagnostics.

## Status evidence

New publication records include an optional UTC `recorded_at` field.
A `source_observation` payload includes its explicit component scope.
A separate `capture_completed` control record identifies its point, components, operation, and completion time.
Source failures use `source_failure` control records with a stable error code and repository scope.
Git-only `source_tracking` records include refs, HEAD, and `components: ["git"]`.
Verification and sampled restore records retain their distinct methods under `repos/<id>/checks/`.
These fields are additive in archive version 1. Older records remain readable; absent times remain unknown.
See [status and recovery evidence](../status.md) for calculation rules and exit codes.
