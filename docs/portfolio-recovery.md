# Local and R2 portfolio recovery

Task T022 runs bounded recovery acceptance on the development machine.
It does not establish final release or full portfolio performance acceptance.
Live S3 and B2 recovery remains in T054. Full-size measurements remain in T051.

## Run the acceptance harness

Install the documented Rust toolchain, native Git, Git Large File Storage (LFS), Python 3, Boto3, and a C compiler.
Use the authorized runtime credentials described in [test resources](test-resources.md).
The GitHub command-line interface must have the fixture creation and deletion permissions.
Looper must supply its resource prefix, artifact directory, and cleanup file.

1. Run `cargo build --locked -p gitpickle`.
2. Source `/home/agent/.config/gitpickle/r2-test.env` in a shell without command tracing.
3. Run `python3 -B tools/gitpickle_portfolio_recovery.py run`.
4. Read `tests/results/recovery/t022.json` and the retained artifact path it contains.

The harness checks tracked and unignored source digests before and after verification.
Build output and generated reports stay ignored. Keep source files unchanged during the run.
The harness never changes project configuration, shared refs, host networking, or unrelated services.

## Scenarios and evidence

The local acceptance suites cover upload and publication faults, job reconstruction, rate limits, scheduling fairness,
missing LFS, unavailable wikis, source loss, rewritten history, and corrupt recovery data.
Their verbose logs identify each executed test.
The scheduler's synthetic 404-job checks do not measure a full portfolio of Git repositories.

The live sequence creates one owned private GitHub fixture and a unique R2 prefix.
It protects only that repository through the GitHub adapter and durable protection service.
It captures Git, checks unchanged payload reuse, rewrites history, deletes captured refs, and transfers real LFS content.
It terminates another process after a real R2 pack upload, before publication.
It removes the local cache, reconstructs the accepted job from R2, and retries without creating a second publication.

Separate deterministic repositories exercise linked wiki and LFS recovery on R2.
An incomplete point cannot become complete until required LFS and wiki content exists.
The unchanged capture reuses its points without Git or LFS payload puts.
Three small repositories include an empty repository and two repositories with history.
These fixtures establish bounded correctness, not full-size performance.

Private GitHub wiki availability is reported separately.
A local wiki fixture does not establish live GitHub wiki transport support.
An unavailable GitHub wiki is never reported as captured.

The harness deletes the private source through its ownership-checked cleanup registration.
It then removes source fixtures and the entire test cache.
The installed executable restores historical Git, current Git/LFS, linked wiki, and small portfolio points directly from R2.
Each restore checks exact refs and reports Git integrity and component verification.
Elapsed times are single bounded samples, not regression baselines.

Recovery runs with GitHub credentials removed and an empty home directory.
A process resolver guard allows only the R2 hostname.
Negative probes confirm that `github.com` and `api.github.com` cannot resolve in that process.
The embedded Git reader separately disables network transports and credential helpers.
The guard does not change the host resolver or firewall.

A fresh R2 download reconstructs the SQLite job projection and capture indexes.
The restored job states must match their durable terminal states.
Corrupting a downloaded pack must stop recovery and leave the destination absent.
Remote archive data remains intact during that corruption test.

## Resource cleanup

The harness preserves existing Looper cleanup registrations.
Before remote creation, it registers standalone cleanup outside the worktree.
Each R2 put first flushes an exact key and SHA-256 digest into the ownership journal.
Cleanup validates the operation prefix and compares downloaded content before deleting exact journaled keys.
It retains unknown keys, changed content, and unexpected multipart uploads for operator recovery.
Repeated cleanup succeeds when owned keys are absent. The persistent bucket is never deleted.
The GitHub provisioning helper registers its own repository ownership checks before creation.

Use the receipt, retained cleanup runner, and original resource prefix for manual recovery.
Source the documented credentials, then run:

```sh
python3 /absolute/artifact/t022-SUFFIX-cleanup.py cleanup /absolute/artifact/t022-SUFFIX.json ORIGINAL_PREFIX
```

Looper runs this cleanup after the operation ends.
Interrupted runs retain their receipts and registrations until cleanup recovery completes.

## Recorded acceptance

The live run passes on 15 October 2026, from 15:17:35 to 15:23:06 UTC.
The run ID is `t022-7ea79473ac0f4c619f6c1178`.
Its private GitHub fixture has repository ID `1420934331`.
The fixture is deleted before recovery. A subsequent authenticated lookup returns HTTP 404.
The original cache and source fixtures are absent during all storage-only restores.

| Check | Observed result |
| --- | --- |
| Private GitHub capture to R2 | Verified point; 8.856 seconds |
| No-change capture | Same point; zero Git or LFS payload puts |
| History rewrite and ref deletion | Earlier branch and annotated tag recover with exact original refs |
| Live private GitHub LFS | Deterministic object uploaded, captured, and recovered with digest verification |
| Process death after R2 pack upload | Exit 71; no publication before retry; one publication after reconstruction |
| Required components on R2 | Incomplete LFS/wiki point retained; complete linked point verified after content becomes available |
| No-change component capture | Same main and wiki points; zero Git or LFS payload puts |
| Storage-only recovery | Six restores pass; each checks exact refs and Git integrity |
| Linked wiki restore | Git, LFS, and wiki verified; exact wiki refs match |
| Small portfolio | Empty, one-commit, and three-commit repositories recover |
| Index reconstruction | Five durable completed jobs and five selected repository indexes reconstructed from R2 |
| Corruption | Downloaded pack corruption fails recovery; destination remains absent |
| Source preservation | Tracked and unignored file snapshot matches before and after verification |

The six restore samples range from 11.563 to 13.471 seconds.
Each includes a fresh download of the bounded archive, rather than a repository-only transfer.
These samples do not establish portfolio throughput or performance regression limits.
The R2 journal records 159 put intents for 109 unique keys.
Its SHA-256 digest is `0993af2ebdb18c2ad28b4318ec3b0db1954fcafe3941745d2df7cbb27684c1ac`.

Raw reports, restore results, local test logs, ownership receipts, and the journal remain in the Looper artifact directory.
The report copy is `tests/results/recovery/t022.json`.
The retained report is `t022-7ea79473ac0f4c619f6c1178/result.json` under that artifact directory.
The focused local suites pass 76 tests. `cargo xtask check` also passes, including 186 Python tests.
Five focused cleanup tests verify repeated removal and rejection of foreign scope, changed bytes, and unknown keys.
Repeated live cleanup removes all 109 owned keys and confirms no unfinished multipart uploads.
The private repository is absent, and the persistent bucket remains intact.

The private GitHub account returns `has_wiki=false` after an enable request.
Live private GitHub wiki capture is not verified. The wiki result above uses a deterministic local source on real R2.
No live S3/B2, full-size portfolio, fresh-VM deployment, or final release acceptance result is claimed.
