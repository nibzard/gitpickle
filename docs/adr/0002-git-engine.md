# Select native Git behind the engine boundary

Date: 15 October 2026. Task: T008.

## Decision

Select native Git for backup staging and standard Git Smart HTTP for the hosting implementation.
Keep writable hosting disabled until T035 and T036 prove its publication coordinator.
Do not add walgit to the application dependency graph.
This decision permits backup work to proceed independently of writable hosting.

The application `GitEngine` port accepts isolated staging paths and returns an inventory or integrity result.
`NativeGitEngine` implements that port. It preserves symbolic and detached HEAD, refs, and the Git hash format.
Its `writable_hosting()` capability returns false.
Local integrity success does not mean that an archive is committed or independently recoverable.
Archive publication, recovery evidence, authorization, LFS coverage, and repository mode remain separate responsibilities.
T023 integrates standard Git HTTP with committed, authorized generations.
The comparison server in the spike is a local test fixture. It is not a product hosting endpoint.

The previous unused port required hosted generations and recovery evidence from the Git engine.
The revised port makes the tested staging behavior explicit and keeps archive authority outside the engine.
The existing backup prototype already uses native Git with its separate archive publication protocol.
No Git negotiation or packet implementation is introduced here.

## Candidate and integration

The inspected fork is `/home/agent/walgit`, at revision
`3e772010b715555522156a2de1a315ef925526c1`. The checkout remains clean.
Its branch is `feat/compare-endpoint`, rather than the proposal's original `main` assumption.
The specified fork also includes snapshot functionality.
The decision applies to this revision, rather than to an assumed upstream feature list.

The fork exposes `walgit_server::AppState::new`, `router`, `walgit_wal::Registry`, and a store trait.
The linked probe builds one executable with path dependencies on the fork's Rust libraries.
It does not run the fork's command-line executable as a managed child service.
Native Git remains a subprocess dependency for its default serving and pack paths.

GitPickle pins Rust 1.89.0. The fork pins Rust 1.97.1 and declares Rust 1.90 at workspace level.
The spike uses the fork's pinned toolchain without changing GitPickle's toolchain.
A fresh integration cannot resolve `bisync ^0.3.0`: the available 0.3 releases are yanked.
Retaining the fork's lockfile permits the private integration build.
Any future adoption must retain a reviewed dependency lock and make toolchain costs explicit.

The fork's root license is MIT. Redistribution requires its copyright and permission notice.
The full workspace metadata contains 588 packages, including platform-specific and optional dependencies.
All declare license expressions. This count is not the set linked on Linux.
The retained inventory records every package, version, license expression, and registry source.
It includes MIT/Apache alternatives, Unicode, ISC, BSD, Zlib, two MPL-2.0 packages,
and LGPL alternatives where permissive choices exist. License declarations alone are not a distribution audit.
The server enables the default S3 and Google Cloud Storage dependency sets.
Major groups include the AWS SDK, Google clients, gix, Tokio, Axum, rustls, Prost, and authentication libraries.
The native fallback adds no Rust dependency. Packaging must disclose the separate native Git executable and its license.

## Adoption gate

The fork has a useful publication boundary: `smart.rs` awaits `publish_push_synced` before building final ref results.
Its emulated WAL suite tests landed writes, lost replies, local apply failure, conflicts, and orphan logs.
These tests support further evaluation; they do not establish provider durability by themselves.

The provider integration gate fails for direct adoption under GitPickle's current verified contract.
The fork's `walgit-store/src/s3.rs` automatically selects multipart uploads above its threshold.
It places `If-None-Match` or `If-Match` on multipart completion for conditional writes.
It also uses conditional completion in its compose path.
GitPickle's R2 adapter deliberately rejects conditional multipart publication because that guarantee is unverified.
The [R2 compatibility table](https://developers.cloudflare.com/r2/api/s3/api/) does not establish that missing guarantee.
The live R2 probe validates conditional single PUTs and unconditional multipart separately.
Do not transfer its results to the fork's untested S3 adapter.

B2 conditional creation remains unavailable in GitPickle's adapter.
A single S3-shaped engine store cannot assume that R2, S3, and B2 support identical publication operations.
Direct adoption needs a reviewed store bridge or provider-specific rejection before sending unsupported operations.
That work also needs independent archive snapshots rather than treating the live WAL as the recovery catalog.
These changes exceed this bounded integration decision. Native Git plus the existing archive contract is the simpler current choice.

## Correctness and compatibility

The executable comparison uses identical fixed sources for both engines.
It tests private HTTP credentials, protocols v0/v2, SHA-1 and SHA-256, empty repositories,
annotated tags, branch and tag deletion, force-with-lease, atomic rejection, and partial multi-ref results.
It captures rewritten history into two independent archive points and restores each retained view.
The probe also tests LFS basic upload/download, acknowledged updates after cache loss,
lost manifest responses, and rejected pushes during storage failure.

A bounded portfolio uses 404 namespaces: 400 small repositories and four larger repositories.
It reuses the fixed sources to keep payloads small and compare the engines with identical inputs.
Every namespace is pushed and its refs are checked. Selected namespaces are cloned and checked with `git fsck`.
This tests namespace handling and a small mixed workload. It does not establish full portfolio performance.

The ordinary native Git HTTP comparison has no LFS service.
GitPickle captures required LFS data separately; existing component tests verify missing data, digests, and recovery.
The fork's successful LFS probe does not grant completeness to a Git-only snapshot.
Private source credentials stay with the authenticated source adapter.
The fixture token is synthetic, accepted only on a free loopback port, and passed through a private environment.
The probe does not exercise GitHub credentials or publish an external endpoint.

A native `post-receive` hook that exits with failure still permits a successful client push.
The regression test records this result.
A hook alone therefore cannot implement the required final-response gate.
Standard HTTP hosting must serve committed generations and keep mutation isolated until archive publication.
T035 owns that integration proof. T036 owns hosted mutation.
Shallow and filtered clones, signed pushes, LFS locking, generic hooks, and the full hosting matrix remain T037 gates.

## Evidence and limits

See [the evidence summary](evidence/git-engine-v1.json) for revision hashes, observed results, and raw artifact locations.
The comparison retains 108 clone samples. Both engines pass all correctness checks.
Raw samples retain each clone duration rather than turning local timing into a speed claim.
The candidate uses an unoptimized development build; stock Git is the installed system executable.
This shared development machine does not provide an exclusive performance runner.
The fixture inventory records object digests, object counts, packed sizes, and refs.
The portfolio timing includes creation, pushes, inventory, and sampled clones.
Neither comparison measures actual cloud request costs, full-size throughput, or deployment latency.

The fresh R2 conformance run uses an owned operation prefix and registers cleanup before writes.
Cleanup checks content ownership and runs twice. The original cleanup registrations remain intact.
Deterministic provider tests retain R2 multipart rejection and B2 conditional-write rejection.
Archive tests exercise publication crashes, response loss, corrupt readback, rewritten history, and source loss.
No acknowledged backup or push is inferred from a local-only write.

T051 retains full-size performance validation on the final pinned VM.
T054 retains actual S3/B2 conformance, crash recovery, and provider-specific hosting evidence.
Their deferred external resources do not block this fallback boundary.

## Repeat the spike

Run from the repository root. The runner reads the clean fork and writes generated files only under ignored directories.
Populate the fork's locked dependency cache before the offline integration build.
The fork and its pinned toolchain are prerequisites for this optional adoption probe, not for the native adapter tests.

```sh
cargo +1.97.1 fetch --locked --manifest-path ~/walgit/Cargo.toml
python3 -B tools/gitpickle_engine_spike.py --output tests/results/git-engine/comparison.json
cargo +1.97.1 test --locked --manifest-path ~/walgit/Cargo.toml \
  --target-dir "$PWD/.cache/walgit-target" -p walgit-wal --test wal -- --test-threads=2
cargo xtask check
```

Use the existing [R2 procedure](../r2-conformance.md) to repeat provider verification.
It requires the authorized runtime credential handoff and Looper cleanup variables.
No S3 or B2 live result is claimed.

Revisit adoption after a provider bridge passes the same tests, a clean integration retains its dependencies,
and the candidate reduces measured operational cost without coupling backup progress to hosting.
