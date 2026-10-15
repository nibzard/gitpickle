# Implementation reviews for T034

Reviewed on 15 October 2026 by the coding agent against source revision
`9457babd2cba1e43493941793c306d8b7a069c5e`, with the T034 documentation changes.
These are completed local code and evidence reviews, not an independent audit or final release approval.
T056 requires review receipts for the final candidate, including later implementation changes.

## Storage and protocol review

| Boundary inspected | Finding | Disposition |
| --- | --- | --- |
| `tools/gitpickle_archive.py`: `put`, `durable_put`, `_publish` | Local bytes are flushed before exclusive publication. Dependency restoration precedes the publication record. | Accept within filesystem durability assumptions. Incomplete components remain explicitly incomplete. |
| `tools/gitpickle_cloud.py`: `CloudArchive.put`; `crates/adapters/src/archive_store.rs`: `persist` | Remote conditional write and exact readback precede local visibility. Existing conflicting bytes stop publication. | Accept for declared conditional-create providers. T006 adds separate B2 content-addressed publication with remote readback; live acceptance remains T054. |
| `tools/gitpickle_writer.py`; archive inventory | One writer, per-repository serialization, ownership records, and fork rejection preserve the stated coordination boundary. | No distributed fence. Operator isolation remains mandatory before takeover. |
| `tools/gitpickle_hosting.py`: `generation`, `handle`; `crates/adapters/src/git_engine.rs` | Reads reconstruct a complete committed generation. Receive-pack is rejected before execution. | M3 is read-only. T035's response gate does not enable daemon pushes. |
| `tools/gitpickle_recovery.py`; archive reader | Storage inventory, exact digests, refs, clean staging, and Git integrity drive recovery. | Whole-prefix downloads and buffered objects prevent a full-size performance claim. |
| Provider conformance and T022 reports | Real R2 readback, response-loss reconciliation, source removal, and recovery exist. | S3/B2 and live private GitHub wiki results remain missing. |

The review found stale archive documentation that described cloud capture as future work.
T034 corrects it and links the implemented cloud bridge and R2 evidence.
The gateway and engine cap responses at 64 MiB. T057 already owns large-transfer streaming.
Conditional multipart publication remains unsupported on R2; do not replace it with an unconditional write.
No retention enforcement or administrator-resistant immutability follows from conditional creation.

## Model-aware review

The Temporal Logic of Actions (TLA+) models cover bounded safety properties, not the entire implementation.
The [model documentation](../../spec/README.md) maps actions to concrete fault boundaries and retained counterexamples.

| Model | Concrete boundary reviewed | Limits |
| --- | --- | --- |
| `ArchiveCommit` | `_publish` checks dependency closure before publication; verification remains separate. | The model checks closure before manifest creation. Code can leave an unpublished manifest, then rejects incomplete publication. |
| `JobReplay` | `tools/gitpickle_jobs.py` records acceptance and stable outcomes; retries reconcile capture identity. | Finite jobs and attempts; liveness assumes responsive dependencies and no crashes. |
| `PlanApproval` | `Plans._approved` and `Plans.execute` revalidate bindings and consume once before restore effects. | Atomic model execution abstracts fallible writes. An interrupted consumed operation can remain `unknown_outcome`. |
| `GitPublish` | Native publication probe delays successful results until publication; crashes reconstruct committed state. | Probe scope only. M4 daemon integration and provider acceptance remain T036/T037/T054. |

The reviewed assumptions require one active writer, complete dependency inventories, collision-resistant digests, and persistent durable objects.
They exclude malicious storage administrators, lost accounts, distributed leases, and provider data disappearance.
Approval checks assume a trusted policy and authentication boundary and no clock rollback across the approval window.
Local process-exit tests exercise writes hidden inside abstract model actions.
No refinement proof or exhaustive provider proof is claimed.
Empty repositories and differing dependency graphs have implementation tests; the archive model uses a shared finite dependency set.
Retention remains outside these models and unavailable in the product.

## Security review

| Boundary inspected | Finding | Disposition |
| --- | --- | --- |
| `tools/gitpickle_daemon.py`: authentication and dispatch | Token digests, fixed principals, exact schemas, protocol headers, origin rejection, and body limits precede dispatch. | Keep tokens in runtime secret channels. Do not expose management through the Git proxy. |
| `tools/gitpickle_plans.py`: approval and execution | Actor, destination, versions, scope, digest, expiry, and grants are rechecked. Parent directories are pinned. | An agent cannot approve by supplying a boolean. A changed destination needs new approval. |
| `tools/gitpickle_security.py`; GitHub adapter | Git configuration and hooks are disabled; child processes have limits; source origins and DNS addresses are constrained. | Same-user hostile filesystem writers remain outside the boundary. |
| Hosting and local MCP | Each Git request requires repository permission. MCP exposes seven tools, not shell or arbitrary URL execution. | Remote MCP, anonymous Git, and writable hosting remain unavailable. |
| Recovery root and archive bytes | Digests detect changed bytes. A separately retained root digest detects root replacement. | Digests alone do not authenticate a wholly replaced archive. Version 1 has no application encryption. |
| Packaging and deployment | Dependency notices, private files, non-root runtime, and pinned artifacts are documented. | Project distribution license is unresolved in T058. No external security audit or authenticated public release channel is claimed. |

The review corrects the security guide's blanket rejection of non-loopback binding.
The explicit container mode can bind `0.0.0.0`; host port publication must remain private.
It also corrects the management example to include required protocol headers.
The operation table describes the core restore service; negotiated workflow and agent capabilities add operations.

## Review conclusion

The inspected implementation supports the bounded claims in the [compatibility matrix](../compatibility.md).
Local code review is complete for this snapshot. Final candidate review and all missing scenario evidence remain in T056.
No final receipt is generated from this document. A documentation review cannot satisfy unrun release checks.
