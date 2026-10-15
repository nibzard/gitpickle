# Archive publication and job replay

These bounded models specify the protocol before its durable transitions exist.
They do not prove the Rust implementation or any storage provider correct.
Retention and writable Git publication remain outside these models.

## Assumptions and bounds

- One active archive writer serializes repository mutations. Two attempts can replay one operation.
- Completed individual writes are atomic, immutable, and visible to reads and listings.
- Durable objects do not disappear. A crash erases all staging and scheduling state.
- Digests uniquely identify validated content. Corruption, hostile administrators, and account loss require separate controls.
- `Objects` is the complete transitive inventory of required Git, LFS, wiki, and base objects.
  Both points require this inventory. Three objects in `pr` and five in `nightly` exercise closure.
  Metadata-only empty points and different dependency graphs require later format-model extensions in T010.
- `Readback` abstracts successful checks of every required component for one immutable point.
  It does not represent a sampled restore or a permanent health assertion.
- `Jobs` represents scoped actor, operation, and deduplication keys. Arguments include repository scope.
  Two argument digests exercise explicit conflicts. Outcome identity derives from the accepted request, never the attempt.
- Responses can be lost indefinitely: durable mutation and response delivery are separate actions.
  Repeated actions represent duplicate delivery. Writes can remain as safe unreferenced objects.
- Source access can disappear at any boundary. It never removes durable history.
  Replay can finish an already captured effect without source access; these models do not promise new capture during source loss.
- Safety has no fairness, clocks, state constraints, or retry limits. Stuttering represents indefinite outages.
  `liveness` separately assumes accepted requests, usable credentials, sufficient capacity, responsive dependencies,
  no crashes, and weak fairness for each job's work. It checks eventual terminal records.
  Safety suites include crashes and source loss and make no unconditional progress claim.
- `pr`: one repository, two points, three objects, two attempts; one job, two argument digests.
  `nightly`: five objects and two jobs. This adds independent job interleavings, not distributed writers.
  Cleanup and restore pinning await the retention model. No automatic deletion is authorized.

## Run the checker

Use Java 21 and Python 3.10 or later. TLC release 1.7.4 contains checker version 2.19 of 08 August 2024.
The runner verifies the distribution with SHA-256 before execution:
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.
The download URL is pinned in `tools/gitpickle_model.py`. A supplied JAR must have the same hash.

```sh
cargo xtask model --suite pr
cargo xtask model --suite nightly
cargo xtask model --suite liveness
cargo xtask model --suite regression
```

Use `--jar /path/to/tla2tools.jar` for offline execution. Use `--timeout 120` to set a per-model deadline.
The runner pins one worker, seed 1, fingerprint polynomial 0, and a 1 GiB Java heap.
The JVM is restricted to major version 21; the report records the exact runtime build.
No external services, credentials, or servers are required.

Each run creates an ignored directory in `spec/results/` with the Looper resource prefix.
It contains logs, checker states, specification and configuration hashes, exact constants,
assumption hash, state counts, runtime, command, Java version, and completion status.
A timeout is `incomplete` and returns failure. A crash, parse error, or missing completion marker cannot pass.
The regression suite succeeds only when TLC returns an invariant counterexample.
Keep generated downloads in `spec/tools/`. Never commit generated state directories.

## Action mapping and fault boundaries

| Model action | Code boundary | Fault injection and acceptance check |
| --- | --- | --- |
| `Capture`, `Upload` | Local `_capture`, `_publish`, `durable_put`; provider `Storage.put` and multipart `complete` | Lose staging; lose response after write success; reconcile by archive digest |
| `Manifest` | Local `_publish`, before/after manifest boundaries | Crash before and after write; validate complete dependencies before publication |
| `Publish` | Local `_publish`, before/after publication boundaries | Crash before and after record; reconstruct from archive without local database |
| `Reply` | Local `_capture`, before reply boundary | Lose response; replay the operation ID without another publication or source access |
| `Readback`, `Verify` | Local `observe_and_verify`; domain `Verification` | Missing required LFS blocks evidence; publication alone never grants verified status |
| `Accept`, `AcceptReply` | `Jobs.accept`, `_append`; `before_accepted` / `after_accepted` | Lose response or SQLite after acceptance; request remains discoverable from archive |
| `Deliver`, `Reject` | `Jobs.tick`, `_execute`, argument comparison in `accept` | Duplicate delivery with two attempts; changed arguments with the same scoped key conflict |
| `Commit`, `TerminalReply` | `Jobs._finish`, `_append`; `before_terminal` / `after_terminal` | Crash before and after terminal write; replay yields the same outcome |
| `Crash`, `SourceLoss` | Restart reconstruction; domain `SourceAccessLost` | Remove all local state and source access; retain committed data and evidence |

The local filesystem coordinator implements the archive boundaries in `tools/gitpickle_archive.py`.
`tools/test_gitpickle_capture.py` injects faults and real process exits at those boundaries.
The coordinator also checks full closure after writing a manifest and before publication.
An unreferenced incomplete manifest can remain as a safe leak; it is never a published point.
The abstract model gates manifest creation earlier. Both paths gate complete publication on durable closure.
These checks do not establish cloud provider behavior.
`tools/gitpickle_jobs.py` implements the local durable job service; see [its contract](../docs/jobs.md).
Current provider-neutral operations live in `crates/application/src/storage.rs`.
Their emulator fault tests live in `crates/adapters/tests/storage.rs`.
Later implementation tasks must test each boundary against real I/O, including every fallible step inside an abstract action.
Review requires a storage/protocol reviewer and a model-aware reviewer before release.

## Counterexample regressions

`Unsafe = TRUE` deliberately removes closure gating or substitutes attempt identity for stable outcome identity.
The regression configurations must fail `CompletePointIsClosed` and `ReplayIsConsistent` respectively.
Retained TLC traces are in `spec/regressions/`.
`test_model_incomplete_closure_regression` suppresses a required pack upload and verifies that publication fails.
It covers the unsafe archive trace against the local coordinator.
`test_interrupted_capture_reconciles_before_cancellation` replays the job trace against the concrete coordinator.
It retains the committed outcome across process loss, source loss, and a competing cancellation.
Do not replace a failing correct configuration with an unsafe configuration.

## Authorized execution plans

`PlanApproval.tla` models authorization before plan application.
The first safety exploration completed before the application service was implemented.
The model stores one immutable approval snapshot. Each member of `changed` represents
one field that differs from that snapshot. Nine fields represent actor, repository IDs,
destination, visibility, expected versions, digest, expiry, impact limits, and operation.
This abstraction explores every combination of changes without enumerating path strings.
It assumes canonical encoding, collision-resistant digests, and trusted authentication and policy loading.
Repository data and model-supplied booleans cannot create an approval.

Logical time has three values: 0, 1, and the expiry boundary 2.
Grants and repository versions can change after approval or queue acceptance.
The single-writer service serializes consumption and records it before filesystem effects.
Crashes remove the queue but preserve consumption. Duplicate deliveries can execute at most once.
`ApprovalMatchesExecution` checks authorization at execution time, rather than against later policy changes.
`SingleUse` checks the execution count. No fairness or progress property is asserted.
The `pr` and `nightly` configurations use these same complete finite bounds.
The unsafe regression permits execution without revalidation. Its trace executes a model boolean without approval.
`test_boolean_and_untrusted_operator_cannot_approve` preserves that failure as an implementation regression.

| Model action | Local application boundary | Acceptance evidence |
| --- | --- | --- |
| `Approve` | `Plans.approve`, immutable approval record | Trusted approver and exact digest; agent boolean denied |
| `Change` | Candidate comparison and canonical digest | Every bound field changed, with and without a recomputed digest |
| `Revoke`, `AdvanceVersion`, `Tick` | `_validate`, called at approval, application, and execution | Grant expiry, revocation, policy revision, repository publication, plan expiry |
| `Queue` | `plan_apply`, `before_accept` / `after_accept` | Durable acceptance, lost reply, concurrent duplicate delivery |
| `Execute` | `execute`, `after_consume` / `after_restore` | Real Git restore, single use, redacted audit, pinned destination parent |
| `Crash` | Immutable acceptance and consumption records | Restart and real process exit after consumption deny replay |

The concrete service uses private local storage and Linux directory descriptors.
It holds policy and repository locks through restore. A destination parent remains pinned during native Git calls.
An interrupted consumed restore reports `unknown_outcome`; an operator inspects the destination before preparing a new plan.
This deliberately sacrifices automatic retry rather than repeating a filesystem effect with uncertain completion.
It does not prove provider durability, hostile administrator resistance, or clock synchronization.
The server clock must not roll back across an approval window.
The model's atomic execute abstracts several fallible writes; fault tests exercise those boundaries separately.
