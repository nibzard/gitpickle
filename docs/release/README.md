# Operator release preparation

**Development documentation only. M3 and the public release are not complete.**
GitPickle remains the working name. T033 controls naming clearance; T056 controls final acceptance.
This documentation does not authorize publication.

Use the [operator guide](../operator.md), [recovery procedure](../recovery.md),
[archive specification](../archive-format/README.md), [compatibility matrix](../compatibility.md), and [deployment guide](../deployment.md).
Protected mirrors remain read-only. Explicit hosted mode is implemented; complete M4 acceptance remains T037.
See the [T052 implementation record](../implementation-acceptance.md). Later connectors and unavailable capabilities are listed in the compatibility matrix.
The [jar and mascot concepts](identity.md) remain private until clearance.

## Available evidence

The following measurements were recorded on 15 October 2026 by earlier tasks.
They are historical observations, not measurements repeated by T034 or benchmarks of every later revision.
Each linked report describes its fixture, environment, method, and limits.
Raw reports remain outside the source repository; local summaries do not replace those reports.

| Evidence | Recorded observation | Scope and limitation |
| --- | --- | --- |
| [R2 conformance](../r2-conformance.md), T007 | 50 provider responses; two successful responses deliberately discarded and reconciled | Three listing keys, bounded conditional writes, and a 5 MiB plus 17-byte multipart payload. No enforced retention. |
| [Portfolio recovery](../portfolio-recovery.md), T022 | Private capture: 8.856 seconds. Six restores: 11.563–13.471 seconds each | Small fixtures; each restore downloads the bounded archive. GitHub credentials and source/cache removed. Not a clean machine. |
| T022 no-change | Zero Git/LFS payload puts; original points reused | Main and linked wiki fixtures; not a 404-repository transfer baseline. |
| [Local deployment](../deployment-acceptance.md), T029 | Initial readiness: binary 2.522 seconds; container 2.803 seconds | One sample per stage, tiny repository, R2 reconstruction, shared host, local test HTTPS. |
| T029 Git access | Initial clone: 0.246188/0.245023 seconds; warm fetch ranges: 0.146954–0.164495/0.131481–0.145498 seconds | Binary/container respectively. Twelve warm fetches per profile. Host networking; standard bridge DNS failed. |
| [Terminal interface](../tui.md), T025 | First paint 163 ms; warm paint p95 164 ms; search p95 0.073 ms; peak client memory 30.75 MiB | 404 registered entries with no captured payload; five warm invocations and 1,000 queries; debug executable. |
| [Local archive economics](../archive-format/tradeoffs.md) | Retained comparison of full bundles and reusable packs | Tiny local fixture. Counts and timings do not establish provider performance. |

The R2 conformance journal digest is `644d3b153ad409fa0290674a4633e56c3bc4651e18df182477742b33768402ef`.
The T022 journal digest is `0993af2ebdb18c2ad28b4318ec3b0db1954fcafe3941745d2df7cbb27684c1ac`.
Use the linked reports to locate retained artifacts and confirm their integrity before final acceptance.
Missing or inaccessible raw evidence must remain missing; do not infer a pass from these summaries.

## Local review and verification

[Implementation reviews](reviews.md) record the storage/protocol, model-aware, and security review findings for T034.
Run `cargo xtask check` for formatting, linting, Rust tests, and Python acceptance tests.
Run `cargo xtask model --suite pr` and `cargo xtask model --suite regression` for bounded safety and deliberate counterexamples.
Keep generated results under ignored directories or the external Looper artifact directory.
The [validation guide](../validation-gates.md) defines final receipts, candidate binding, noise calibration, and invalid-run rules.
A passing local suite cannot satisfy an absent final stage.

## T034 verification record

The local run on 15 October 2026 passes `cargo xtask check`: formatting, linting, Rust tests, and 238 Python tests.
The Python suite takes 241.008 seconds. This duration is not a performance baseline.
All four safety models complete: ArchiveCommit (568 states), JobReplay (194), PlanApproval (30,720), and GitPublish (57,720).
The deliberate unsafe configurations each produce their expected invariant counterexample.
Documentation checks validate 73 local links and parse both SVG assets.
A source snapshot comparison confirms unchanged contents and modes for 223 tracked and unignored files during final verification.

Raw logs and model reports are retained under:

```text
/home/agent/.looper/gitpickle-c39a4efa/runtime/task-64744fd74151d855/work-00-session-0a1202dbc4cbffea/
```

| Artifact | SHA-256 |
| --- | --- |
| `t034-check.log` | `1c74f44194efac757254263a7b2b3b7b173c577c295010342387382410a888ba` |
| `t034-model-pr.json` | `e28146483ee34bb3fd2da7fe640ce09ecd94aeed55e5fdb3e11d3e0d1bc14970` |
| `t034-model-regression.json` | `42e4d04d86601b721fe319e6a0ece2a0c6044c1ffd39ce371f14aeaf7b72637e` |

The same directory retains each model log, `t034-doc-check.json`, and the source snapshot.
No external resources are created by this documentation task. Existing cleanup registrations remain unchanged.
No live provider tests, full-size benchmarks, real-user study, or fresh-machine drill run in T034.

## Section 8 acceptance register

T056 owns the final evidence record for every row below. Earlier tests establish bounded local behavior only.

| Required scenario | Available implementation evidence | Remaining final evidence |
| --- | --- | --- |
| Source account unavailable | T022 removes private source; status and recovery suites retain old points | Candidate-bound provider and deployment results, including freshness behavior |
| Force-push or branch deletion | T022 restores exact historical refs | S3/B2 results in T054 and final candidate review |
| Incomplete LFS | Component suites and R2 incomplete/complete recovery | Final provider coverage; live private GitHub wiki remains unverified |
| Daemon dies during upload | Capture process-exit matrix; T022 R2 upload crash | Final S3/B2 crash and reconstruction evidence |
| Disk disappears after success | T022 rebuilds five durable completed jobs; T029 reconstructs 13 records | Clean-machine recovery and every fresh deployment profile in T055/T056 |
| Two jobs target one repository | Writer locks, job replay tests, bounded models | Final contention and candidate-bound protocol review |
| Direct push conflicts with mirroring | Mirrors reject pushes; hosted mode pauses source updates; local and R2 publication checks pass | Complete M4 compatibility evidence in T037 |
| Agent changes approved destination | Plan mutation, expiry, revocation, and replay tests | Final security/model review receipts |
| Clean-machine recovery with GitHub blocked | Local installed binary restores from R2 with resolver guard | Fresh-machine, independent key/software, and GitHub-blocking evidence in T055/T056 |
| Hot path regresses | Comparison, confirmation, noise, and waiver tooling | Full-size measured baseline on final `pve2` profile in T051 |
| Representative-user acceptance | Scripted product contracts only | T032: at least five users; four complete setup, identify incomplete coverage, and restore unaided; every user distinguishes configured, verified, and fresh |

## Final gate ownership

T056 must resolve measured real-user failures, apply the final cleared name, and collect every remaining section 8 result.
It also requires the full-size baseline, clean-machine recovery, compatibility matrix, and final security/model/protocol review receipts.
T032 and T033 are final gates, not prerequisites for preparing these documents.

| Existing task | Unmet outcome |
| --- | --- |
| T032 | Operator recruits users and records actual study outcomes; T056 resolves measured failures. |
| T033 | Operator obtains the written naming determination and completes clearance last; T056 applies the decision consistently. |
| T051 | Full-size application/provider measurements, both comparison baselines, and noise characterization on the final pinned VM. |
| T053/T054 | Authorized S3/B2 access, actual conformance, recovery, and applicable hosted durability evidence. |
| T055 | Fresh systemd and Coolify profiles, standard bridge networking, actual private HTTPS/tailnet access, and storage-only recovery. |
| T037 | Complete M4 compatibility, provider crash, contention, and latency contract. |
| T058 | Operator selects the project distribution license before public distribution. |
| T056 | Final candidate acceptance, remaining evidence above, and truthful release claims; no publication authorization. |

Do not run the 10 GiB workload on the development machine.
Do not treat an administratively closed prerequisite as evidence of credentials, permissions, or a completed test.
No full-size baseline, real-user findings, naming clearance, or clean-machine result is supplied by T034.
