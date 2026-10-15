# Validation gates

Run the same commands locally and in continuous integration (CI).
Keep evidence outside the source repository. The runner rejects paths inside it.
Generated source exports use ignored `.cache/gates/` directories.
No shared Git references or repository settings change.
The [implementation acceptance record](implementation-acceptance.md) maps T052 dependencies to bounded evidence.

## Local acceptance

1. Set an evidence directory outside the checkout:

   ```sh
   export GITPICKLE_EVIDENCE=/absolute/path/to/validation-evidence
   ```

2. Run the pull request suite:

   ```sh
   cargo xtask validate --suite pr --evidence "$GITPICKLE_EVIDENCE"
   ```

3. Run a specific fault matrix:

   ```sh
   cargo xtask fault-test --scenario archive-commit --evidence "$GITPICKLE_EVIDENCE"
   ```

The pull request suite runs formatting, linting, Rust tests, Python contracts,
bounded safety models, deliberate counterexamples, liveness, deterministic
small benchmarks, application capture benchmarks, and the fault matrix.
The fault command also accepts `job-replay`, `approval`, and `all`.
It uses real publication and process-exit tests, rather than simulated exit codes.
Each bundle preserves model counterexamples and their code mapping.
Unexpected model failures and timeouts fail the suite.
Only the deliberate regression configurations expect counterexamples.

The reference benchmark measures native Git and local storage emulation.
The application benchmark measures the concrete archive implementation.
Neither establishes cloud performance or full-size acceptance.

## Compare revisions

Use immutable commit identifiers for the release baseline.
Set the CI variable `GITPICKLE_RELEASE_BASELINE` to the reviewed release commit.
Do not advance it to hide a regression.

```sh
cargo xtask compare-revisions --candidate HEAD --merge-base main \
  --release-baseline RELEASE_SHA --repetitions 30 \
  --evidence "$GITPICKLE_EVIDENCE"
```

The runner resolves the merge base and both comparison revisions.
It exports clean sources into three private directories and records their commits.
Each export has a private Git repository for existing benchmark metadata queries.
It uses the same installed toolchain for all runs.
It reverses execution order on alternate repetitions.
Each repetition generates equivalent fixtures and fresh scenario state.
Setup stays outside each timed scenario. Raw reports preserve cache definitions.
Operating system caches remain uncontrolled; cold means a fresh destination.
Two repetitions are useful for smoke checks. They cannot confirm timing budgets.

The output includes separate candidate, merge-base, and release reports.
Both baseline comparisons remain visible. Fixture and runner mismatches fail.
A failure in any repetition produces `invalid.json` and a nonzero exit code.

## Noise, confirmation, and waivers

Measure two independent clean runs of the same baseline on the pinned runner.
Use at least 30 repetitions per run. Retain both raw evidence bundles.

```sh
cargo xtask calibrate --baseline baseline.json --repeat baseline-repeat.json \
  --pinned-runner pve2-profile-v1 --evidence "$GITPICKLE_EVIDENCE"
cargo xtask gate --candidate candidate.json --baseline baseline.json \
  --confirmation candidate-repeat.json --noise noise/report.json \
  --evidence "$GITPICKLE_EVIDENCE"
```

Run `gate` separately for the merge base and the pinned release baseline.
Characterize noise separately for each baseline and fixture version.
The runner records the observed range for each metric as its noise floor.
Minimum floors are 10 milliseconds, 1 MiB for memory and disk, and 0.01 CPU seconds.
No numeric timing block applies before noise characterization.
A regression above 5% produces a warning and requires another run.
A block requires both candidate confidence intervals to exceed the baseline
interval by 10% and the calibrated absolute floor. Each run needs 30 samples.
Resource metrics use their own floors. Every storage request change is reported.
Material request increases require reviewer rationale, even when timing passes.

Use `--waivers waivers.json` for a reviewed JSON array. Each entry contains:

```json
{
  "scenario": "empty-and-tiny/no-change",
  "owner": "reviewer-name",
  "explanation": "Reason for accepting the measured cost",
  "correctness_tradeoff_assessment": "No correctness loss",
  "expires": "2026-12-01T00:00:00Z",
  "next_target": "Reduce the measured cost within the revised budget",
  "baseline_commit": "exact-baseline-sha",
  "candidate_commit": "exact-candidate-sha"
}
```

A waiver applies only to its exact scenario and commit pair until expiry.
It stays in the report. It does not replace the release baseline.
Lost acknowledged updates, missing closure, failed authorization, invalid fixtures,
missing samples, and no-change payload uploads fail before waiver handling.

## Nightly and release acceptance

```sh
cargo xtask validate --suite nightly --evidence "$GITPICKLE_EVIDENCE"
cargo xtask validate --suite release --evidence "$GITPICKLE_EVIDENCE"
```

Nightly uses the larger model configurations and the crash/replay matrix.
Absent final stages produce explicit pending task identifiers and exit code 2.
A local pass cannot turn a pending final gate green.
Release also requires security, model, and protocol review receipts.

Use `--stage-config /private/path/stages.json` to execute final acceptance stages.
The file maps a stage name to a command argument array. It contains no secrets.
The command receives `GITPICKLE_GATE_EVIDENCE` and `GITPICKLE_GATE_STAGE`.
It writes `<stage>.json` in that evidence directory with schema version 1 and the exact source commit,
`status: passed`, a `checks` map, and links in `raw_evidence`.
A zero command exit code without a complete receipt remains invalid.
The stage names and required checks are explicit in `final_stages` in
`tools/gitpickle_gates.py`.

For example, this configuration runs a provisioned full-size acceptance driver:

```json
{
  "full-size-performance": {
    "command": ["/opt/validation/full-size-driver"],
    "profile": {
      "host": "pve2",
      "exclusive": true,
      "hardware": "recorded pinned VM hardware",
      "network": "recorded network profile",
      "allocation": "owned AgentLab allocation identifier",
      "limits": "recorded CPU, memory, and disk limits"
    },
    "minimum_free_bytes": 214748364800
  }
}
```

The acceptance driver runs `compare-revisions --bench-suite all --profile full`,
records large/cold fixture evidence, request amplification, resource ceilings,
and separate application/provider results. It writes its receipt after validation.
It must use the same baseline and confirmation policy described above.
Full-size runs require the exclusive pve2 profile and at least 200 GiB free disk.
T051 executes them after T055 supplies the final VM profile.
Do not run them on the development machine.

S3 and B2 canaries have separate stage configurations and credential environments.
T054 supplies their actual conformance and recovery evidence after T053 access.
R2 uses the existing conformance and portfolio recovery runners.
All provider commands must register ownership-checked cleanup before mutations.
T055 supplies systemd, Coolify, private HTTPS, and bucket-only recovery evidence.
T056 supplies the review and release recovery receipts.
These tasks retain their original acceptance requirements.

## CI provisioning and evidence

`.github/workflows/validation.yml` runs local acceptance on pull requests.
It compares both clean baselines when the release baseline variable exists.
An absent baseline fails the comparison step instead of passing silently.

Scheduled and release jobs use the `gitpickle-pve2` runner label.
Each stage uses a separate protected GitHub environment.
Provision its command configuration at `/etc/gitpickle-validation/<stage>.json`.
Provide provider credentials through that environment's `STORAGE_CONFIG`,
`STORAGE_ACCESS_KEY`, and `STORAGE_SECRET_KEY` secrets.
No provider credentials enter pull request jobs.
The runner creates a cleanup file and preserves registrations until cleanup runs.
Do not include shared services in cleanup.

Each job uploads raw logs, samples, models, and reports as CI artifacts.
Final jobs also copy evidence to independent object storage.
Set `EVIDENCE_DESTINATION`, `EVIDENCE_ACCESS_KEY`, and `EVIDENCE_SECRET_KEY`
in each environment. Set `EVIDENCE_REGION` and `EVIDENCE_ENDPOINT` variables.
Use an evidence account separate from the source repository account.
An absent destination or failed upload fails the archival step.
Retain artifacts according to the release retention policy.
Local runs retain raw evidence directly outside the checkout.

Deterministic increases in storage requests require an explicit rationale.
The runner uses `review-required` until you supply `--request-rationales`.
Use the same entry fields and commit bindings as the waiver file.
A performance waiver does not supply a request rationale.

For a pinned comparison, add `--confirm --pinned-runner PROFILE_ID` to
`compare-revisions`. This runs a second alternating series, characterizes each
baseline's noise, and applies both confirming comparisons automatically.
Each series requires at least 30 repetitions. Use `--scenarios no-change`
for a bounded investigation of that scenario.
The output includes a concise HTML table and links to raw JSON evidence.

CI combines local and external receipts in its final acceptance job.
Missing, duplicate, failed, or mismatched stage evidence cannot pass that job.
The local job accepts exit code 2 only when external acceptance is pending.
The aggregate job must resolve every pending stage for the selected suite.
Each stage receipt uses `schema_version: 1`. Its raw evidence paths must exist.

## Moderated usability study

The [study package](usability/README.md) defines T031 preparation and T032 execution.
It includes scenario reset steps, a synthetic rehearsal, and empty evidence templates.
Rehearsal checks do not establish participant success. T032 requires actual representative-user evidence.
T056 retains the final usability acceptance decision and correction of measured failures.
