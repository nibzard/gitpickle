# Implementation and local acceptance

T052 completes the development implementation checkpoint on this machine.
This checkpoint does not complete M3, M4, or public release acceptance.
GitPickle remains a working name. No publication is authorized.

## Implementation scope

The executable includes the command-line interface, daemon, optional terminal interface, and local Model Context Protocol server.
They share authorization, durable execution, archive, and recovery services.
Native Git handles the Git protocol behind the engine adapter selected in T008.
Protected mirrors reject pushes. Explicit hosted mode permits durable pushes and basic LFS uploads.
Source updates remain paused until explicit reconciliation resumes mirror mode.

Capture writes native pack files with a 32 MiB target instead of capturing pack bytes through diagnostic stdout.
Git can exceed this target for a single object. Each pack must fit the 5 GiB conditional single-write limit.
The archive manifest lists every pack. Existing version 1 readers restore these packs without a format change.
Initial capture and recovery pass for a single incompressible 65 MiB blob.
A separate HTTPS regression captures two 33 MiB blobs together and verifies the resulting clone.
The storage bridge still buffers individual packs. These checks do not establish full-size memory limits.

## Dependency acceptance index

Every dependency has concrete implementation and bounded acceptance coverage.
The local suite executes all `tools/test_*.py` suites and Rust workspace tests.
The linked documents describe fixtures, implementation boundaries, and evidence limits.

| Task | Implementation and bounded evidence |
| --- | --- |
| T007 | Rust storage adapters, signed transport, deterministic conformance; actual [R2 conformance](r2-conformance.md). |
| T008 | `GitEngine` boundary, native Git fallback, retained [engine decision](adr/0002-git-engine.md). |
| T012 | Archive publication, readback, every immutable write fault, unchanged payload reuse; `test_gitpickle_capture.py`. |
| T013 | Independent inventory, historical restore, integrity checks, bundle export; `test_gitpickle_recovery.py` and [recovery](recovery.md). |
| T014 | Durable jobs, replay after projection loss, fair bounded scheduling, cancellation; `test_gitpickle_jobs.py`. |
| T015 | Authenticated discovery, stable IDs, future repositories, 404-entry scheduling; `test_gitpickle_github.py`. |
| T016 | Required historical LFS and linked wiki coverage, incomplete points, later recovery; `test_gitpickle_components.py`. |
| T017 | Freshness and component calculations, scoped restore evidence, check exit codes; `test_gitpickle_status.py`. |
| T018 | Bound approvals, revocation, changed destinations, replay denial; `test_gitpickle_plans.py` and `PlanApproval`. |
| T019 | Management authentication, path isolation, sanitized child processes, redaction; `test_gitpickle_security.py`. |
| T020 | Guided workflow, durable job IDs, scope, noninteractive errors; `test_gitpickle_workflow.py`. |
| T021 | Nonsecret recovery cards, unknown archive versions, negotiated client versions; recovery and workflow suites. |
| T022 | Private GitHub capture, source loss, LFS/wiki fixtures, cache removal, [R2 portfolio recovery](portfolio-recovery.md). |
| T023 | Real HTTPS clone/fetch/pull, authentication, empty repositories, Git/LFS downloads above 64 MiB; hosting suite. |
| T024 | Seven constrained local tools, paging, approval denial, resumable jobs; `test_gitpickle_mcp.py`. |
| T025 | Keyboard terminal interface, plain alternatives, 404-entry search; `test_gitpickle_tui.py` and [measurements](tui.md). |
| T026 | Versioned bundles, dependency notices, unprivileged container, restart and upgrade; package and deployment suites. |
| T027 | Reviewed installation, SSH identity, resumable recipes, private HTTPS configuration; `test_gitpickle_setup.py`. |
| T029 | Local binary/container lifecycle and R2 reconstruction; [deployment acceptance](deployment-acceptance.md). |
| T030 | Local/CI gates, invalid evidence rejection, paired baselines, noise and waiver rules; gate/model/performance suites. |
| T034 | Operator and recovery documentation, [implementation reviews](release/reviews.md), explicit final gates. |
| T035 | Native atomic/partial results, final-response gate, process loss; publication suite and `GitPublish`. |
| T036 | Hosted creation/conversion, checkpoint, paused source jobs, durable pushes/LFS, lost-response replay; hosting/cloud suites. |

The [compatibility matrix](compatibility.md) records provider and protocol boundaries.
R2 supports the verified publication path. S3 has deterministic adapter coverage and requires live final acceptance.
B2 remains recovery-only because this adapter does not establish conditional creation.
Do not infer B2 publication from an S3-compatible endpoint. T054 must retain this explicit capability gate.
Live private GitHub wiki transport remains unverified; local wiki fixtures on R2 establish linked recovery only.

## Repeat local validation

1. Use the dependencies in the [development guide](development.md) and [local environment guide](local-test-environment.md).
2. Build the executable with `cargo build --locked -p gitpickle`.
3. Run `cargo xtask validate --suite pr --evidence "$LOOPER_ARTIFACT_DIR/t052-final"`.
4. Source `/home/agent/.config/gitpickle/r2-test.env` with shell tracing disabled.
5. Run `python3 -B tools/gitpickle_hosted_acceptance.py` in the same shell.
6. Read the external suite and hosted reports. Require passed checks and `source_unchanged: true`.

The local suite includes formatting, linting, Rust/Python tests, four bounded safety models,
deliberate counterexamples, conditional liveness, small benchmarks, and the publication/job/approval fault matrix.
Two benchmark repetitions exercise correctness and semantic no-change checks. They do not establish timing budgets.
Pack generation remains an `Upload` action in `ArchiveCommit`: every generated pack precedes manifest and publication.
The existing fault boundaries apply separately to each pack write. Closure validation still precedes publication.
No model or durability invariant is weakened by splitting pack files.

The R2 runner uses the T022 receipt format and standalone ownership-checked cleanup implementation.
Its receipt therefore uses a `t022-` operation ID; its report identifies task T052.
It registers cleanup before writes and journals exact keys and digests before each upload.
It preserves the persistent bucket, unrelated objects, and existing cleanup registrations.
Repeated cleanup verifies the owned prefix has no objects or unfinished uploads.
The runner uses the real native storage bridge and receive-pack coordinator.
It rejects missing LFS and injected pack storage failure before success.
It deletes the source and cache, downloads from R2, verifies restored Git/LFS data,
reconstructs hosted mode, and replays the committed native response.
HTTPS transport and real Git LFS uploads have separate local client tests.
This bounded provider check does not replace the complete M4 provider crash matrix.

## Evidence location

Raw evidence stays outside this checkout under the operation's `LOOPER_ARTIFACT_DIR`.
The retained local bundle contains `suite.json`, command logs, raw model reports, regression traces, and benchmark samples.
The hosted report is `<operation>/hosted-result.json`; the ownership journal and cleanup receipt are sibling files.
An ignored summary at `tests/results/implementation/t052-hosted.json` points to that retained report.
Both runners check tracked and unignored file contents and modes before and after validation.
Generated output belongs in ignored paths. No shared references or repository configuration change.

## Final gates

These existing tasks remain mandatory. They do not block this development checkpoint.

| Task | Remaining evidence |
| --- | --- |
| T037 | Complete M4 standard-client, provider crash, contention, resource, and durability-latency acceptance. |
| T051 | Full-size measurements on the final pinned `pve2` VM, both baselines, and noise characterization. |
| T053 | Operator supplies authorized S3/B2 access through runtime credentials. |
| T054 | Actual S3/B2 conformance, capture/recovery, capability decisions, and hosted durability evidence. |
| T055 | Fresh AgentLab VM deployments: systemd, Coolify, private HTTPS/tailnet, and bucket-only recovery. |
| T032 | Representative-user study: five users, four successful unaided workflows, all distinguish configured/verified/fresh. |
| T033 | Written naming determination, owner decision, and applicable clearance checks. |
| T058 | Operator selects the project distribution license. |
| T056 | Resolve study findings, apply the cleared name, complete section 8 and final candidate reviews/recovery receipts. |

No live S3/B2, fresh-VM, representative-user, full-size performance, naming-clearance, or release-complete result is claimed here.
Do not run full-size fixtures on this development machine.
