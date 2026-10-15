# GitPickle

GitPickle is a proposed self-hosted application for independent Git repository recovery.
The executable supports storage-only recovery of version 1 archives, inventory previews, and full Git bundle exports.
Recovery requires native Git and Python 3. It works without the original server or GitHub.
The executable also provides read-only status and doctor queries for local archives.
The CLI and authenticated daemon provide guided protection, durable capture jobs, status, and recovery.
R2 and S3 publication use the storage adapters with remote readback. Hosting remains under development.
GitPickle remains a working name pending naming clearance.

Read [the proposal](gitpickle.md) for the product requirements.
Read [storage-only recovery](docs/recovery.md) for commands and current limits.
Read [status and recovery evidence](docs/status.md) for health checks and evidence scopes.
Read [local agent tools](docs/mcp.md) for MCP profiles, grants, and approval requirements.
Read [development setup](docs/development.md) for prerequisites and workspace boundaries.

With Rustup and a native linker installed, run these commands from the repository root:

```sh
rustup toolchain install 1.89.0 --profile minimal --component rustfmt --component clippy
cargo build --workspace --locked
cargo xtask check
cargo run --locked --package gitpickle -- --help
```

The checks need no provider credentials, running services, or private repositories.

See [the guided command-line workflow](docs/cli-workflow.md) for setup, automation, and recovery.
