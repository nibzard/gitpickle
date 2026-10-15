# Development

## Prerequisites

Install Rustup and a native C linker. On Debian-based Linux, `build-essential` supplies the linker.
The workspace pins Rust 1.89.0, Rustfmt, and Clippy in `rust-toolchain.toml`.
Run all commands below from the repository root.

```sh
rustup toolchain install 1.89.0 --profile minimal --component rustfmt --component clippy
cargo build --workspace --locked
cargo xtask check
cargo run --locked --package gitpickle -- --version
```

`cargo xtask check` stops on the first failure and returns a nonzero exit code.
It checks formatting without rewriting files, runs Clippy with warnings denied, and runs workspace tests.
Tests include unit tests, documentation tests, and the executable contracts in `tests/cli_contract.rs`.
The executable package registers that root-level contract test explicitly.
All targets and features are linted. All workspace features are tested.

The domain and application use only the Rust standard library and local crates.
Interfaces pin Serde and serde_json for wire serialization and contract tests.
Local dependency versions are exact. `Cargo.lock` is committed.
Use exact versions for future external dependencies and review lockfile changes.
Builds and tests use `--locked` to reject dependency resolution changes.
After installing the toolchain and fetching locked dependencies, checks can run without network access:

```sh
cargo fetch --locked
cargo build --workspace --locked --offline
CARGO_NET_OFFLINE=true cargo xtask check
```

Install native Git and Git Large File Storage (LFS) before developing Git adapters or generating repository fixtures.
The workspace was checked with Git 2.47.1 and Git LFS 3.4.1.
These are recorded development versions, not a product compatibility guarantee.
Check your installation without changing Git configuration:

```sh
git --version
git lfs version
```

The benchmark contract tests invoke Git and query the LFS version.
Do not run a global `git lfs install` for workspace setup.
Fixtures keep their configuration and LFS payloads inside their disposable directories.

## Model checking

TLA+ (Temporal Logic of Actions) specifications use the TLC model checker.
Model development requires a Java runtime and the `tla2tools.jar` distribution of TLC.
Reserve `spec/tools/` for the downloaded distribution and `spec/states/` for generated states.
Use `java -version` to inspect the runtime.

Run `cargo xtask model --suite pr` for bounded safety checks.
Run `--suite nightly`, `--suite liveness`, and `--suite regression` for the other checks.
The runner pins TLC 1.7.4 by checksum and requires Java 21.
Java and TLC are not prerequisites for `cargo xtask check`.
Retained T009 reports are in `spec/evidence/`; generated reports remain in ignored `spec/results/`.
See [the specification directory](../spec/README.md) for assumptions, bounds, fault mappings, and evidence.

## Workspace boundaries

| Component | Responsibility | Allowed local dependencies |
| --- | --- | --- |
| `domain` | Value types and invariants | None |
| `application` | Shared use cases and ports | Domain |
| `adapters` | Git, GitHub, storage, and database implementations | Domain, application |
| `interfaces` | Command-line and future transport interfaces | Application |
| `gitpickle` | Executable and composition root | Application, adapters, interfaces |
| `xtask` | Development checks, fixtures, and benchmark harness | None |

Keep provider, database, and transport types outside the domain.
Add modules within these crates before adding crates for individual entities.
T002 adds typed contracts and architecture enforcement.
See [the architecture decision](adr/0001-domain-and-contracts.md) and [user contracts](product/contracts.md).
The executable supports help, version, local archive status, doctor, storage-only recovery,
and authenticated local management. See [security boundaries](security.md) for `serve`.
See [status and recovery evidence](status.md). Unsupported commands return exit code 2.

## Generated files

Run `cargo xtask dev-env` before local development acceptance tests.
See [local test isolation](local-test-environment.md) for resource ownership, cleanup, and machine limits.

Cargo writes into ignored `target/`. Keep the default target directory for local checks.
Use ignored `.cache/` for local tool caches, including a workspace-local `CARGO_HOME`.
For example, `CARGO_HOME="$PWD/.cache/cargo" cargo xtask check` keeps Cargo's cache inside the workspace.
Generated fixtures belong in `fixtures/generated/`; reports belong in `perf/results/`.
Model downloads, states, and reports belong in `spec/tools/`, `spec/states/`, and `spec/results/`.
Source fixtures, specifications, budgets, and report schemas remain tracked.
Setup and verification must not rewrite source files or `Cargo.lock`.
Use `cargo fmt --all` only when intentionally editing source formatting.

For Looper runs, name temporary resources with `LOOPER_RESOURCE_PREFIX`.
Register idempotent cleanup in `LOOPER_RESOURCE_CLEANUP_FILE` before creating external resources.
Check ownership before removal and preserve existing cleanup entries.
The checks create no external provider resources. Transport tests bind loopback servers to free ports and close them after each test.

## Fixture and benchmark tooling

Install Python 3.10 or later for the standard-library benchmark harness.
`cargo xtask check` also runs its fixture, storage-fault, recovery, and evidence-validation tests.
The tests use ignored local directories and do not require external credentials.
See [fixtures](../fixtures/README.md), [benchmarks](../benches/README.md), and [performance evidence](../perf/README.md).

## Authorized plans

See [authorized plans](authorized-plans.md) for the shared local service and trusted authentication boundary.
The normal Python acceptance suite checks binding changes, grant revocation, expiry, replay, redaction, and managed destinations.
The `pr`, `nightly`, and `regression` model suites include `PlanApproval.tla`.

See [the CLI workflow](cli-workflow.md) for guided commands, runtime credentials, provider configuration, and acceptance coverage.
