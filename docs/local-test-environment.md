# Local test environment

This shared machine supports development tests and small exploratory benchmarks.
It has no exclusive processor, disk, or network allocation.
Do not generate full-size fixtures or run heavy benchmarks here.
Full-size measurements remain in T051. Fresh virtual machine profiles remain in T055.
These tasks also cover independent evidence storage, private HTTPS, Tailscale, and Coolify acceptance.

## Run the preflight

Looper supplies `LOOPER_RESOURCE_PREFIX`, `LOOPER_RESOURCE_CLEANUP_FILE`, and `LOOPER_ARTIFACT_DIR`.
Outside Looper, set these variables to a unique prefix, a private cleanup script, and an evidence directory.

1. Run `cargo xtask dev-env`.
2. Read the JSON report at the printed path under `tests/results/dev-env/`.
3. Run `CARGO_BUILD_JOBS=2 cargo xtask check`.
4. Run the small benchmark below when you need reference measurements.

```sh
cargo xtask bench --suite quick --profile smoke --repetitions 1 \
  --scenarios initial-capture,no-change,verified-restore \
  --output "${LOOPER_RESOURCE_PREFIX}-quick"
```

The benchmark writes ignored JSON and HTML under `perf/results/`.
One repetition checks the harness. It cannot establish a timing regression or full-size performance.
The benchmark measures native Git with local storage. It cannot establish application or provider performance.
Use a unique output label for each run.

## Resources and cleanup

The preflight records tool versions, processor details, memory, free disk, and available control group limits.
It requires Rust, Cargo, Rustfmt, Clippy, Git, Git Large File Storage (LFS), Python 3, and a C linker.
Java is recorded for model checking. Hyperfine and Podman are optional.
The workspace pins the Rust toolchain. Model checking requires Java 21 and the checksum-pinned TLC distribution.

The preflight registers cleanup before it allocates resources.
It appends to the existing cleanup script and synchronizes the entry to disk.
The entry contains its cleanup program and resource identifiers, so recovery does not need this checkout.
Cleanup checks the prefix, unique token, artifact directory, and local directory owner.
It preserves directories with missing or different ownership markers and refuses symbolic links as ownership markers.
Cleanup is idempotent. The preflight runs it on exit; Looper can run it again during recovery.
An interruption retains resources until recovery executes the registered cleanup.

Scratch data uses a private temporary directory with the resource prefix and a random token.
The SQLite database stays inside that directory. No shared database is needed.
Git uses private configuration and disables inherited hooks and credentials.
The endpoint binds `127.0.0.1` on port zero. The operating system selects a free port.
The preflight closes every socket before it exits. It does not start a persistent host server.

When Docker and the local `busybox:musl` image exist, the preflight also runs a small container.
Use `--container-image NAME` to select another installed image with `/bin/sh`.
The preflight resolves the image identifier before creation. It never pulls or builds an image.
The container has a unique name and these ownership labels:

- `io.looper.resource-prefix=$LOOPER_RESOURCE_PREFIX`
- `io.looper.artifact=$LOOPER_ARTIFACT_DIR`
- `io.gitpickle.dev-token=<unique token>`

Cleanup inspects the exact container name and requires all three labels before removal.
The container uses no network, volumes, or host ports.
It has a read-only filesystem, an unprivileged user, no capabilities, and explicit processor, memory, and process limits.
An unavailable Docker daemon or image is recorded as unavailable. Local tests can proceed without it.
Once a container starts, a failed container check fails the preflight.
Shared containers, images, networks, volumes, services, and host configuration stay outside cleanup.

The preflight requires 1 GiB of free disk for its small checks.
It verifies Git integrity, LFS availability, SQLite writes, loopback communication, and optional container execution.
It also checks that tracked and unignored project files retain their contents and modes.
Generated reports use explicit ignore rules. No build artifacts are tracked.

The preflight does not install packages or change repository configuration.
It does not establish fresh-machine deployment, HTTPS, provider conformance, or recovery acceptance.
Keep those final checks attached to T051 and T055.

## Machine observation on 15 October 2026

The first preflight passes on this machine with the following recorded environment.
Free memory and disk values describe that run. Run the preflight again before allocating test data.

| Item | Observation |
| --- | --- |
| System | Linux 6.8.0-147-generic, x86-64, glibc 2.39 |
| Processor | Intel Core i9-12900HK model, eight available logical processors, shared virtual machine |
| Memory | 12,246,824 KiB total; 10,821,964 KiB available |
| Disk | 58,047,201,280 bytes free; workspace and temporary directory share the filesystem |
| Rust and Cargo | 1.89.0; Rustfmt 1.8.0; Clippy 0.1.89 |
| Git and LFS | Git 2.47.1; LFS 3.4.1 |
| Python and C compiler | Python 3.12.3; GCC 13.3.0 |
| Model runtime | OpenJDK 21.0.12.1 |
| Containers | Docker server 29.1.3; local BusyBox container check passes |
| Optional tools | Podman and Hyperfine absent |
| Control group | No processor or memory ceiling in visible ancestors; process ceiling 14,305 |

The container uses image identifier `sha256:654fc8fd836e35f4a64586bddf8f59b9029b48cf80f520834c6c98ca8ab5def9`.
Git integrity, LFS availability, SQLite, and communication on a free loopback port pass.
Owned scratch and container resources are removed. Source contents and modes remain unchanged.
The quick reference benchmark passes with one repetition and six capture/recovery scenarios plus five fault scenarios.
These observations establish local tool readiness only. They do not establish a pinned performance baseline.
