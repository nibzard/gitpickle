# Format decision and measured tradeoffs

Choose reusable, non-thin packs with complete manifests for version 1.
Recovery experiments passed before this format document replaced the proposal placeholder.
The independent reader consumes only archive bytes and a new destination.

The experiment compares three generations of identical seeded fixtures for each representation.
Each update adds a commit with the same tree. The incremental strategy uses a full checkpoint,
one prerequisite bundle, and another full checkpoint. Its maximum chain length is one.
The reusable strategy packs only new object IDs, with delta compression disabled.
Full bundles use native Git's normal packing. Empty repositories use metadata rather than bundles.

| Fixture repository | Full bundles, bytes | Reusable packs, bytes | Bounded bundles, bytes |
| --- | ---: | ---: | ---: |
| Empty | 0 | 0 | 0 |
| Tiny | 9,435 | 3,204 | 6,772 |
| Rewritten source | 26,467 | 8,973 | 17,959 |
| LFS source, Git payload only | 13,623 | 4,599 | 12,284 |

Every nonempty strategy writes three Git payload objects. Metadata writes are excluded from this table.
The LFS fixture also requires eight shared objects totaling 32,768 bytes in the smoke profile.
They do not change across generations. The pack reader verifies and restores all eight.
Bundles alone do not preserve LFS content or unborn HEAD, so they still require a manifest.

[Raw evidence](evidence/comparison-v1.json) includes fixture digests, environment, stage durations,
byte counts, request-equivalent filesystem writes, and correctness results.
Run `python3 tools/gitpickle_archive_compare.py` to reproduce the experiment.
The comparison verifies Git integrity and refs after each generation.
It verifies that the incremental bundle fails verification without its prerequisite.
The acceptance suite also removes source repositories and preceding point records before restore.

Full bundles offer simple standalone transport but repeat unchanged objects.
Bounded bundles save bytes between checkpoints but require explicit ordered bases.
Reusable packs avoid repeated payload here and describe recovery through a single complete inventory.
This costs more pack reads as history grows. Compaction needs a new complete generation.
The manifest's cumulative object list also grows; it is not an optimized production index.

These are single local smoke samples, not statistical performance or provider measurements.
Capture timing includes LFS pointer inspection for the reusable prototype; bundle timing does not.
Do not compare these durations as equivalent end-to-end application latency.
The rewritten fixture starts after its source rewrite. Historical retention is tested separately through retained points.
The test uses commit-only changes. It does not quantify large blob deltas or repack amplification.
Disabling deltas makes dependency independence explicit, but can increase bytes for similar large objects.
T030 must measure full-size fixtures, heavy changes, repacking, request costs, and compaction thresholds.
This decision commits the interoperable representation, not a claim that release performance budgets pass.
