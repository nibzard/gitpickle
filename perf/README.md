# Performance evidence version 1

`cargo xtask bench` writes JSON and a static HTML report under ignored `perf/results/`.
The HTML links the JSON with raw samples, storage traces, fixture manifests, and environment metadata.
Keep independent copies of acceptance evidence outside the repository.

The JSON contains these required groups:

| Field | Meaning |
| --- | --- |
| `schema_version`, `record_kind`, `scope` | Version 1, measured evidence, native Git/local storage reference |
| `commit`, `source_dirty` | Source identity and whether tracked files differ from the commit |
| `environment`, `runner_fingerprint` | Architecture, CPU, memory, kernel, filesystem, tool versions, transport, concurrency, measurement method |
| `status`, `expected_scenarios`, `repetitions` | Completion contract, scenario inventory, sample count |
| `rows` | Scenario, fixture manifest/digest/version, cache definition, raw samples, summary, correctness |
| `comparison` | `not_run` or explicit baseline comparison rows |

Each sample has elapsed time, stage durations, RSS, CPU time, disk use, storage counts and bytes,
retained bytes, resource sample count, full storage events, and correctness outcomes.
The executable validator in `tools/gitpickle_perf.py` rejects unsupported versions, missing rows/samples,
invalid metrics, failed correctness, missing measurements, changed fixtures, and no-change payload uploads.
An interrupted run retains `incomplete` status. A caught setup or measurement error writes `invalid` status.
Neither can produce a passing report. Benchmark failures return a nonzero exit code.

Median confidence intervals use 1,000 deterministic bootstrap resamples.
Reports omit p95 below 30 samples. Intervals from small runs remain exploratory.
A comparison requires the same runner fingerprint, fixture version/digest, and cache definition.
A timing increase above 5% warns. Above 10% blocks only with at least 30 samples, nonoverlapping
confidence bounds supporting that increase, and more than 10 ms of absolute change.
Every storage request count change is reported and warns.
No comparison is reported as `not_run`, never as a passing regression gate.

This initial harness does not automate clean baseline worktrees, alternating candidate runs, or release approval.
Run both revisions on the same pinned runner and preserve merge-base and release-baseline result files.
Compare against both files separately. Application and real-provider release gates remain separate.

The portable field contract is [result-schema-v1.json](result-schema-v1.json).
The executable validator also enforces cross-field rules, embedded fixture hashes, trace totals, and raw-sample summaries.
`harness_sha256` identifies the executed harness when tracked source changes are present.
Resource comparisons report absolute and relative changes. They warn above 5% when the absolute floor is exceeded.
Provisional floors are 1 MiB for sampled memory/disk and 10 ms for CPU time.
Comparisons with fewer than 30 samples use `exploratory`, not `pass`.
Confirmed resource increases above 10% block when 30 samples, confidence bounds, and the metric's floor support the increase.
