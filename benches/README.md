# Executable reference benchmarks

Run the dedicated harness through `cargo xtask`:

```sh
cargo xtask bench --suite quick --output candidate
cargo xtask bench --suite portfolio-404 --repetitions 1 --output portfolio-exploratory
cargo xtask bench --suite lfs-2g --profile smoke --output lfs-smoke
cargo xtask bench --suite quick --baseline main --output candidate-v2
cargo xtask perf-report --input candidate-v2 --compare main
```

`--baseline main` reads `perf/results/main.json`. It does not check out or modify a Git ref.
A missing baseline fails the command. Result files cannot be overwritten by a benchmark run.
A JSON path can replace a result label. Use ignored `perf/results/` for local output.
The default result label is `latest`. Choose a new label for each run.

The quick suite covers empty/tiny repositories, rewritten history, and all five storage faults.
Use any fixture name as a suite, or `--suite all` for the catalog.
Use `--scenarios initial-capture,no-change,verified-restore` to select scenarios.
Each scenario resets its mutable repositories, storage, and restore directory between samples.
The default is 30 repetitions. Smaller runs are exploratory and omit p95.

The harness implements a **native Git and local storage reference**, independent of application services.
It provides initial capture, no-change capture, small/heavy changes, warm/cold clone, verified restore,
index rebuild, and process restart. Capture creates complete Git bundles and uploads missing payloads.
No-change capture must upload zero Git or LFS payload bytes. Metadata writes are counted separately.
Restore reads the storage inventory and verifies refs, symbolic HEAD, Git integrity, historical refs, and LFS digests.
The restart scenario starts a fresh Python process that reads the stored inventory, then verifies recovery.
The fault suite checks retries and expected rejection. A detected fault passes only its fault contract.

Native clone uses the local Git transport with `--no-local`. It measures Git data, not LFS checkout.
Cold means a fresh destination and no application cache. It does not mean a cold operating-system cache.
Both cache descriptions are included in results. No HTTP, provider, or application performance is claimed.
Queueing, daemon memory, authentication, scheduling, and application publication require later application adapters.
Criterion microbenchmarks and Hyperfine CLI timing belong to those later measured paths.

Each result records its scope and environment. Timing includes reference work and correctness verification.
Stages separate source observation, object validation, pack generation, upload, publication, inventory,
reconstruction, and final validation. Setup and fixture generation are outside the measured region.
Local file storage records every attempted PUT, GET, HEAD, and LIST, actual bytes, errors, and retries.
Large payload transfers use bounded buffers. This storage emulator is not a provider conformance harness.

Resource measurements require Linux `/proc`. The monitor samples the harness and its descendants every 20 ms.
Peak resident set size (RSS) is a sampled lower bound; short-lived children can fall between samples.
CPU time includes reaped descendants. Disk usage records sampled logical file bytes in the sample directory.
These values include monitoring overhead. They do not claim exact physical disk allocation or daemon-only RSS.
The harness fails if monitoring fails. No external resources, databases, servers, or ports are created.
Temporary directory names use `LOOPER_RESOURCE_PREFIX` when supplied and are removed after each run.

Native clone also records received pack-file bytes. This excludes protocol framing and LFS payloads.
Change scenarios record logical new bytes, changed repositories, and upload amplification.
Zero-change amplification is null; its absolute uploaded payload count must be zero.
Heavy changes add deterministic content equal to one quarter of the repository's logical bytes, with a 4 KiB minimum.
