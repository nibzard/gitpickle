# Storage contract and conformance

The application port is `crates/application/src/storage.rs`.
The emulator and shared conformance harness are in `crates/adapters/src/storage/`.
Provider adapters and their limits are documented in [storage providers](storage-providers.md).
Run the deterministic tests with:

```sh
cargo test --locked --package gitpickle-adapters
```

The port provides object reads, writes, pagination, and multipart uploads.
Object keys reject empty segments, traversal segments, control characters, and backslashes.
Archive digests contain SHA-256 values. Provider revision tokens are opaque comparison values.
A revision token never verifies content. `verified_get` hashes the returned bytes.
The SHA-256 implementation stays in the adapter, outside the domain and application.

A successful write exposes one complete, durable object. Multipart parts remain invisible until completion.
Completion checks the write condition against the current object.
A failed condition leaves the current object unchanged.
Part retries replace the same numbered part. Completion accepts an ordered selection of uploaded parts.
Abort is idempotent and cannot delete a completed object.

`put_immutable` requires conditional creation and limits the number of attempts.
It reconciles unknown outcomes through a read and an archive digest check.
It also checks an existing object after a conditional conflict.
Different content causes an integrity error. It never overwrites an existing object.
Exhausted attempts return an unknown outcome, which does not prove that storage lacks the object.
A lost multipart completion response also requires readback before any new upload.
A lost initiation response can leave an unreachable upload. Provider lifecycle cleanup requires separate authorization.

## Capabilities

Adapters declare completed-write visibility, conditional creation, conditional replacement, and multipart support.
Conditional multipart creation and replacement have separate declarations.
The harness skips conditional multipart checks when that capability is unavailable and reports the omission.
They declare versioning and provider retention separately.
Retention distinguishes unsupported, available, and enforced for the configured scope.
The conformance report does not certify retention or versioning.
Application append-only behavior means the routine storage port exposes no object deletion.
It does not protect against storage administrators or stolen provider credentials.
The emulator does not simulate provider retention enforcement or historical version retrieval.

## Deterministic faults

Queue faults with `Emulator::inject(operation, fault)`.
Each fault applies once, when its operation reaches the front of the queue.
The call log records every attempt. No random source or clock affects the outcome.

- Unavailable: reject the operation before mutation.
- Lost response: perform the operation, then return an unknown outcome.
- Corrupt read: change returned bytes while preserving the revision token.
- Hidden read: hide a completed object for one read.
- Empty page: return an empty intermediate page with a continuation cursor.

Read-specific faults apply to reads. Empty-page faults apply to listing.
Tests exercise successful reconciliation, exhausted retries, stale conditions, and digest failures.
They also exercise missing multipart parts, part retries, invalid completion order, and prefix-bound cursors.

## Safe provider integration

1. Obtain an authorized disposable bucket and scoped credentials.
2. Register idempotent, ownership-checked cleanup in `LOOPER_RESOURCE_CLEANUP_FILE` before creating external resources.
3. Create `TestScope` with `LOOPER_RESOURCE_PREFIX` and a unique run identifier.
4. Pass the adapter and scope to `conformance::run`.
5. Retain the report and provider settings without credentials.
6. Let the registered cleanup remove only owned test resources.

The harness uses `<resource-prefix>/conformance/<run>/` and rejects an occupied prefix.
All initial writes require conditional creation. A collision fails without replacing the existing object.
Conditional replacement uses only a revision from a successful harness creation.
The harness never deletes objects or changes bucket configuration.
It leaves test objects as evidence for the registered cleanup.
Multipart checks use a 5 MiB first part and a short final part.

Listing checks use bounded pages and a bounded traversal.
Empty intermediate pages are valid. Repeated cursors, duplicate keys, and keys outside the prefix fail.
Unsupported optional operations appear in the report.
Missing completed-write visibility or conditional creation rejects the run before mutation.
A compatible endpoint alone does not establish conformance.
T006 implements provider adapters. T007 records live R2 evidence. T054 records live S3 and B2 evidence.
Local emulator results do not establish live provider behavior.

The local suite creates no external resources and requires no provider credentials.
Generated provider reports belong in ignored `tests/results/storage/`.
