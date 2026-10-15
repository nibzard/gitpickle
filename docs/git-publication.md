# Durable Git publication boundary

Task T035, 15 October 2026. This is a bounded integration proof.
T036 integrates the gate with explicit hosted mode and durable LFS uploads.
The isolated probe remains available for fault tests.
The shared packet framing lives in `tools/gitpickle_receive.py`.
T037 owns the complete writable compatibility and fault matrix.

## Tested coordinator

`tools/gitpickle_publish_spike.py` delegates negotiation and ref validation to
native `git receive-pack --stateless-rpc`. It does not implement negotiation.
The HTTP fixture uses a free loopback port and ordinary Git clients.
It has no external credentials or cloud dependency.

1. Hold the repository lock and reconstruct its current committed generation.
2. Run receive-pack against a disposable private repository.
3. Stream outgoing sideband channel 2 packets as progress.
4. Hold channel 1 packets, plain report-status packets, and the final flush.
5. Check native process completion and capture the resulting repository through the archive coordinator.
6. Select visible refs from the durable publication chain.
7. Release the exact retained native report and final flush.

The probe preserves opaque report-status bytes, including nested packet lines.
It does not infer success from the native process exit code.
Native Git can exit successfully when individual refs fail.
The actual staged refs determine the archive generation.
An atomic rejection leaves the old generation. A mixed result archives only accepted updates.
A mixed result still returns a failing client exit status with native per-ref results.
Successful atomic creation and branch deletion also pass.

The gate validates outgoing packet sizes, complete frames, the terminal flush,
and a bounded final report. Progress does not consume the final-report buffer.
A fatal channel, truncated output, or excess report size fails the operation.
Tests cover fragmented frames and plain report-status without sideband.
Native clients exercise both report-status v2 with sideband and plain report-status v1.
This parser is an outgoing response gate, not a general Git protocol implementation.

## Observed failures

A failing `post-receive` hook cannot retract native Git success.
`test_post_receive_failure_does_not_gate_native_acknowledgment` retains that regression.
A pre-receive upload cannot archive the resulting mixed ref set.
A hook operating on the live repository also exposes refs before publication.
The selected approach isolates mutation and gates the final response outside hooks.
Fixture hooks exist only to generate progress and reject one ref.
Source configuration and hooks never enter archive reconstruction.

Closing an HTTP response after progress without a terminal result caused a native
client to wait until the test deadline. The fixture now sends a fatal sideband
packet and final flush on coordinator failure. It never fabricates ref results.
For a plain report-status session, it sends an `ERR` packet instead.
The fixture reads only the first command packet capabilities to select that encoding.
Tests verify storage failure with both negotiated response forms.
A network or process loss can still leave the client with an unknown outcome.

A crash after publication can commit a push whose client reports failure.
Ordinary Git has no application idempotency key. A new push advertises durable refs
and can report `up to date` without another publication.
The probe tests this behavior after both injected exceptions and process exits.
It does not promise to replay the original mixed-result report to a new Git session.
A retry after another writer's later update requires fresh lease and policy checks.
The single-writer deployment contract remains mandatory.

## Model and concrete fault mapping

`spec/GitPublish.tla` passed its first safety check before the probe was implemented.
It models one serialized repository, two generation identities, two attempts,
two refs, and three required objects. The nightly bound uses five objects.
Required objects abstract the complete Git closure and any required components.
Each result records accepted refs, rejected refs, and atomic intent.
A generation identity fixes its manifest result across retries.
Publication, reader visibility, final acknowledgment, crash, and retry are separate actions.
Replies can be lost by crashing before `Reply`. Unreferenced writes can remain.

| Model action | Probe boundary | Executable check |
| --- | --- | --- |
| `Stage` | Restore committed point into `stage.git` | Exit at `staged`; no new refs become visible |
| `Upload` | Archive payload writes | Exit before/after payload; old publication remains authoritative |
| `Manifest` | Archive manifest write and closure validation | Exit before/after manifest; incomplete work remains invisible |
| `Publish` | Archive publication record | Exit before/after publication; reconstruct only the committed chain |
| `Expose` | `point()` reads the publication chain | Progress barrier still exposes the old point |
| `Reply` | `send(final)` | Exit before/after final output; every emitted success restores |
| `Crash`, `Retry` | Delete engine workspace; restart from archive | Retry advertises committed refs; no duplicate publication |

The real process tests use `os._exit` at eleven boundaries.
They discard engine work, retry through a new endpoint, and restore the result.
The progress test waits at `before_publication` and observes hook progress through
a real client while the client remains running and the old generation stays visible.
After release, the client succeeds. Recovery then passes without the source or engine workspace.
Exception tests also cover storage failure and lost replies.

The invariants check published closure, durable acknowledgment, reader isolation,
atomic result rules, immutable replay results, and retained history across crashes.
The unsafe regression releases a staged result before publication.
TLC must find `AcknowledgedPushIsDurable` violated; the retained trace is in
`spec/regressions/GitPublish.trace`.
The concrete before-publication fault and progress barrier cover that failure.
Exact hashes, configurations, counts, runtime, and completion are in
`spec/evidence/git-publish-*.json` and their adjacent logs.

The model assumes one active writer, atomic completed immutable writes, complete
object inventories, valid digests, and no spontaneous loss of durable data.
It has no fairness or unconditional progress claim. Retention remains disabled.
Its abstract retry identity represents an immutable generation record, not a Git wire feature.
An empty accepted set can map to the current concrete generation without a new record.
Model checking does not prove implementation refinement or cloud provider behavior.

## Repeat and integration limits

```sh
python3 -B -m unittest discover -s tools -p test_gitpickle_publish.py -v
python3 -B -m unittest discover -s tools -p test_gitpickle_engine.py -v
cargo xtask model --suite pr
cargo xtask model --suite nightly
cargo xtask model --suite regression
cargo xtask check
```

The client tested here is Git 2.47.1 on Linux. Fixtures contain small SHA-1 repositories.
The probe uses the existing filesystem archive contract, including flush and readback.
It adds staging, reconstruction, closure validation, and durable publication latency to a push.
It does not establish cloud latency or large-transfer performance.
Requests remain bounded fixtures; this is not a streaming request-body implementation.
Generated work belongs under ignored `fixtures/generated/publication/` and
`tests/results/publication/`. TLC output remains under ignored `spec/results/`.

T036 must integrate authorization, repository modes, serialization across all mutation paths,
provider publication, operation reconciliation, negotiated failure handling, and resource limits.
It must retain read-only hosting until that integration passes its tests.
The Git-only probe rejects LFS pointers before publication.
It cannot grant complete recovery coverage to missing LFS or wiki data.
T037 retains client versions, hash formats, lease conflicts, broad ref operations,
LFS policy, contention, and the full crash matrix.
T054 retains actual S3 and B2 evidence. T055 retains fresh deployment validation.
No new external prerequisite is needed to repeat this local proof.
