# 🥒 GitPickle

**Keep your history. Lose the panic.**

Preserve your Git repositories in your own storage. Recover them without GitHub or the original server.

[Get started](#get-started) · [Recover a repository](#recover-a-repository) · [Read the design](gitpickle.md)

## Your commits deserve a long shelf life

- **Your storage.** Publish archives to Cloudflare R2 or Amazon S3, with remote readback before local visibility.
- **Independent recovery.** Restore from archive data without the original daemon, database, or local cache.
- **Evidence you can inspect.** Check coverage, freshness, integrity, and recorded restore tests with `status` and `doctor`.
- **Git you can use.** Recover a bare repository or export a full Git bundle.

**In development.** Guided protection, durable capture jobs, and storage-only recovery are implemented.
Authenticated Git hosting supports protected mirrors and explicit hosted mode with durable pushes.
Bounded local tests and R2 recovery checks support these development claims.
See the [implementation acceptance record](docs/implementation-acceptance.md) for scope and final gates.
GitPickle is a working name pending naming clearance. M3 and public release acceptance remain incomplete.
See the [operator guide](docs/operator.md), [compatibility matrix](docs/compatibility.md), and [release evidence](docs/release/README.md).
See [current behavior and limits](docs/cli-workflow.md) before configuring protection.

## Get started

Build from source with Rustup and a native C linker installed.
Run these commands from the repository root:

```sh
rustup toolchain install 1.89.0 --profile minimal --component rustfmt --component clippy
cargo build --workspace --locked
./target/debug/gitpickle --help
```

Running GitPickle requires native Git and Python 3.10 or later.
Capturing Git Large File Storage (LFS) data also requires Git LFS.

<details>
<summary><strong>Protect repositories and follow capture jobs</strong></summary>

Configure the daemon, scoped credentials, and storage with the [guided command-line workflow](docs/cli-workflow.md).
Then preview protection before applying the saved plan:

```sh
gitpickle protect github --all --preview
gitpickle protect github --resume REFERENCE
gitpickle status
gitpickle doctor --all
```

Replace `REFERENCE` with the saved plan reference from the preview.
These examples assume `gitpickle` is on your `PATH` and the client can reach your configured daemon.
For a source build, use `./target/debug/gitpickle`.

The workflow covers storage setup, authorization, capture jobs, retries, and automation.
Use the [optional terminal interface](docs/tui.md) to search the portfolio and review operations with the keyboard.
Without cloud configuration, the daemon uses a filesystem archive.
Keep an independent archive copy to protect against loss of that machine.

</details>

## Recover a repository

With an existing archive directory, inspect the inventory, preview a restore, then recover:

```sh
gitpickle restore --from-storage /archives/pickle --inventory
gitpickle restore --from-storage /archives/pickle --repository repo123 --dry-run
gitpickle restore --from-storage /archives/pickle --repository repo123 --destination ./recovered.git
```

Use a repository ID from the inventory and a new destination whose parent directory exists.
Recovery checks archive integrity and runs `git fsck --full --strict` before exposing the recovered repository.

<details>
<summary><strong>Recover from cloud storage, select history, or export a bundle</strong></summary>

Recovery supports direct reads from R2, S3, and Backblaze B2.
Supply storage credentials through the runtime environment.
You do not need a GitHub login or a running daemon.

The [recovery guide](docs/recovery.md) covers provider options, historical points, incomplete data, and bundle exports.
It also explains how to retain a recovery card, credentials, required provider keys, and recovery software.

A full Git bundle contains Git data only.
Use bare repository recovery for required LFS objects and an enabled wiki.

</details>

<details>
<summary><strong>Understand coverage and current limits</strong></summary>

Protection covers captured Git history, required LFS objects, and enabled wikis.
Unpushed commits and uncommitted files remain outside that coverage.
GitHub issues, pull requests, releases, packages, and account settings are not included.

A successful upload does not establish verified recovery.
[Status and recovery evidence](docs/status.md) explains what each result proves and which components it covers.

R2 and S3 publication use storage adapters with remote readback.
B2 supports content-addressed publication and recovery under the single-writer contract.
Live B2 acceptance remains pending T053/T054; see [the publication protocol](docs/storage-providers.md#b2-archive-publication).
Live provider validation remains separate from local tests.
See [storage providers](docs/storage-providers.md) for configuration and capability limits.

Version 1 archives have no application encryption.
Direct cloud recovery downloads the entire archive prefix before selecting a recovery point.
See the [recovery limits](docs/recovery.md#current-limits-and-validation) before planning capacity.

</details>

<details>
<summary><strong>Develop and check GitPickle</strong></summary>

Run the workspace checks from the repository root:

```sh
cargo xtask check
```

The checks need no provider credentials, running services, or private repositories.
See [development setup](docs/development.md) for prerequisites, workspace boundaries, and test setup.

For more detail:

- [Product proposal and brand guidelines](gitpickle.md)
- [User contracts](docs/product/contracts.md)
- [Archive format](docs/archive-format/README.md)
- [Security boundaries](docs/security.md)
- [Model specifications](spec/README.md)
- [Model Context Protocol (MCP) tools](docs/mcp.md)

</details>

*In a pickle? Git it back.* 🥒

Deployment bundles, systemd installation, container recipes, dependency licenses,
and artifact checks are documented in [deployment](docs/deployment.md).

Use [guided installation](docs/setup.md) to review local or SSH installation and generate private Coolify recipes.
