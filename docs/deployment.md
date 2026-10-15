# Deployment packages

The first package target is Linux x86-64 with glibc 2.36 or later.
The build pins Rust 1.89.0, Cargo.lock, base image digests, and a Debian snapshot.
Only one daemon can write to an archive. Stop and fence the old daemon before a replacement starts.

## Build and verify

1. Run `python3 tools/gitpickle_package.py`. Output goes to ignored `dist/`.
2. Run `cd dist && sha256sum -c SHA256SUMS`.
3. Extract the versioned archive into a new directory. Inspect `build.json`, `NOTICES.txt`, and `sbom.cdx.json`.
4. Run the extracted `gitpickle --version`. Keep this bundle with your offline recovery material.

Build on Debian Bookworm for the stated glibc minimum. Host builds inherit the host's libc minimum.
For a pinned build, run `docker build --target build --output type=local,dest=dist/build -f deploy/Dockerfile .`.
This exports the builder filesystem; release artifacts are under `dist/build/out/`.
Use `docker build -t gitpickle:0.1.0 -f deploy/Dockerfile .` for the runtime image.
Export it with `docker save -o dist/gitpickle-0.1.0.docker.tar gitpickle:0.1.0`.
Docker save produces a Docker image archive, accepted by Docker load; it is not an OCI layout.
To produce an Open Container Initiative (OCI) layout archive, use a separate output file:

```sh
docker save -o dist/runtime-docker.tar gitpickle:0.1.0
python3 -B tools/gitpickle_oci.py dist/runtime-docker.tar dist/gitpickle-0.1.0-linux-amd64.oci.tar
```

The converter verifies each uncompressed layer against the image configuration.
It prints the immutable OCI manifest digest. Retain that digest with the archive checksum.
The layout includes `index.json`, `oci-layout`, and content-addressed blobs.
For an authorized registry handoff, an OCI tool can copy this archive to the registry.
Record the resulting registry digest before deployment.

Repeat builds in the same pinned environment and compare `SHA256SUMS`.
The tar format normalizes order, owner, modes, timestamps, and gzip headers.
The compiler removes the source directory from embedded paths.
This does not promise identical bytes across host toolchains or CPU architectures.
Image build inputs are pinned. Image manifest timestamps can vary between builders.
After an authorized registry upload, record the returned `name@sha256:...` reference.
Use that immutable reference for both standalone and Coolify deployments. Never deploy `latest`.
No registry publication is required for local tests. Image IDs pin local Docker deployments.
Checksums detect changes; they do not authenticate a publisher.
Obtain the expected checksum or image digest through an independently authenticated release channel.
No signing identity or public release is configured by this task.

The CycloneDX software bill of materials (SBOM) records locked Rust packages, license expressions, and registry checksums.
The bundle retains available dependency license and notice files.
The container retains Debian license records in `/usr/share/doc/*/copyright` and common licenses in `/usr/share/common-licenses/`.
It records every installed package version in `/usr/share/gitpickle/runtime-packages.tsv`.
Its SBOM also includes the Debian packages and hashes of their license notices.
Native Git uses GPL-2.0-only. Git LFS uses MIT. Python uses PSF-2.0 and its bundled license notices.
The `prlimit` command comes from util-linux. It is required for subprocess limits.
Git HTTP uses native Git. No walgit code is linked.
The project currently has no declared distribution license. Resolve this before public distribution.

For reviewed local installation, SSH installation, and private HTTPS, use [guided setup](setup.md).

## VM installation

Use a dedicated unprivileged account with a systemd user manager. Do not run the daemon as root.
Install Python 3.11 or later, Git, Git LFS, util-linux, and CA certificates through your operating system.

1. Verify and extract the bundle under `/opt/gitpickle/releases/0.1.0/`. An administrator can create this directory.
2. Point `/opt/gitpickle/current` at that release. Only the administrator should modify release files.
3. Create `~/.config/gitpickle/` with mode `0700`. Create `policy.json` and `runtime.env` with mode `0600`.
4. Copy `deploy/systemd/gitpickle.service` to `~/.config/systemd/user/gitpickle.service`.
5. Run `systemctl --user daemon-reload` and `systemctl --user enable --now gitpickle`.
6. Check `curl --fail http://127.0.0.1:8080/readyz` and `journalctl --user -u gitpickle`.

Enable lingering for the service account through your administrator if it must run without a login session.
The service creates its private state directory through `StateDirectory`.
Set the policy sandbox to an existing private directory beneath that state directory.
The policy schema and grants are documented in [security.md](security.md).
The service binds to loopback. Hosting stays disabled until explicitly configured with a canonical HTTPS URL.
A systemd override can add the hosting arguments. Keep the policy file outside the cache.

To upgrade, stop the service, verify the new bundle, and change `current` to the new release.
Start the service and check readiness, the reported version, and archive status.
Rollback uses the previous verified bundle only when it supports the current archive format.
Never run two release versions against one writable archive.

## Runtime credentials

Supply these variables through a private runtime file or the deployment platform's secret controls:

```text
GITPICKLE_MANAGEMENT_TOKENS={"REPLACE_WITH_RANDOM_32_PLUS_CHARACTER_TOKEN":{"actor":"owner","agent":false}}
GITPICKLE_GITHUB_TOKEN=REPLACE_AT_RUNTIME
GITPICKLE_SERVER_ID=REPLACE_WITH_A_STABLE_INSTALLATION_ID
GITPICKLE_STORAGE_CONFIG={"provider":"r2","endpoint":"https://ACCOUNT.r2.cloudflarestorage.com","region":"auto","bucket":"BUCKET","prefix":"ARCHIVE/"}
GITPICKLE_STORAGE_ACCESS_KEY=REPLACE_AT_RUNTIME
GITPICKLE_STORAGE_SECRET_KEY=REPLACE_AT_RUNTIME
```

Use the quoting rules of your runtime file parser. Systemd and Compose accept single quotes around JSON values.
Do not add these values to command arguments, image layers, Compose source, or Git.
For a durable deployment, require the storage configuration. Without it, the archive is local filesystem data.
Loss of that filesystem loses a local-only archive.
R2 is the locally verified provider. Live S3/B2 acceptance remains in T054.
Store required recovery keys separately from the daemon and cache volume.
Keep the server identity in runtime configuration across restarts and cache loss.
A different installation must use the explicit fenced takeover procedure.

## Standalone containers and Coolify

Use `deploy/compose.yaml` for both standalone Compose and a Coolify Compose service.
Set `GITPICKLE_IMAGE` to the verified image digest. Set `GITPICKLE_RUNTIME_ENV` to the private runtime file.
Set `GITPICKLE_POLICY` to an absolute policy file path. Its owner must be UID 10001, with mode `0600`.
For containers, use `/var/lib/gitpickle/restores` as the policy sandbox.
The image creates that private directory. Do not mount secrets into the cache volume.
Run `docker compose -f deploy/compose.yaml config --quiet` before starting the service.
Then run `docker compose -f deploy/compose.yaml up -d` and inspect health and logs.
For a standalone Coolify image service, preserve the image's command and health check.
Use UID 10001, the same mounts, runtime secrets, stop timeout, and host loopback port binding.

Keep replicas at one. Disable overlapping rolling updates; stop the old writer before starting its replacement.
Do not configure a public Coolify domain for a private deployment.
The explicit `--container-network` option permits the isolated container listener.
Publish it only on host loopback. Use host Tailscale Serve for private HTTPS.
Network access does not replace management or repository authorization.
For optional public HTTPS, use the restricted proxy recipe in [guided setup](setup.md#optional-public-https).

The root filesystem is read-only. Only the cache volume and bounded temporary filesystem are writable.
The process runs as UID 10001 without capabilities. Logs use stdout and stderr.
`/livez` and `/readyz` return a minimal status without authentication or repository details.
Readiness means startup reconstruction and writer acquisition finished. It does not prove fresh backups or current upstream access.
Use `doctor` for coverage and recovery evidence. Startup download time grows with archive size.
Adjust the health check start period for your measured archive startup time.

SIGTERM stops new request admission and drains active workers before releasing archive ownership.
The stop timeout permits the native Git operation limit plus shutdown time.
A forced stop can leave interrupted work. Durable records determine replay after restart.
Never treat container health as proof of a committed capture.

## Restart and disk-loss checks

Stop the daemon before deleting any cache. Keep policy, runtime credentials, recovery keys, and remote archive data.
Restart with an empty private state directory. The daemon downloads archive records and rebuilds local indexes.
Confirm accepted jobs and committed recovery points through status, then perform a verified restore to a new directory.
Do not delete or migrate a local-only archive under this procedure.
Use `python3 -B -m unittest discover -s tools -p 'test_gitpickle_deployment.py'` for local package lifecycle checks.
Use the cloud composition tests for remote acceptance and complete cache-loss reconstruction.
Fresh systemd, Coolify, private HTTPS, and remote-client acceptance remains in T055.

## Local acceptance evidence

See [deployment and recovery acceptance](deployment-acceptance.md) for T029 commands, local measurements, fixtures, and the T055 procedure.

On 15 October 2026, the package passes installation, restart, release-path replacement, and SIGTERM checks under the local systemd user manager.
Run `python3 -B tools/gitpickle_systemd_acceptance.py` after building the bundle to repeat these checks.
This uses private unit names, a free port, and ownership-checked cleanup.
Set `GITPICKLE_UPGRADE_BINARY` to a different compatible binary to test an upgrade between versions.

The pinned container passes the same lifecycle with an R2 archive and a new empty volume.
All 13 immutable archive and job records match after reconstruction. A native Git restore preserves the committed file.
Run the Docker/R2 acceptance with the authorized test credentials in the environment:

```sh
python3 -B tools/gitpickle_deployment_acceptance.py --image sha256:IMAGE_ID --upgrade-image sha256:CANDIDATE_ID
```

The checker requires Looper resource variables, Docker, and noninteractive sudo for the policy file owner.
It registers cleanup before mutation. It journals remote object digests and labels each container and volume.
It retains nonsecret results under `LOOPER_ARTIFACT_DIR`.
The development host's Docker bridge cannot resolve external names, including with its upstream resolver.
The local passing run uses `--host-network` and free loopback ports. The build uses `docker build --network host`.
These options do not change shared network settings. The standard bridge recipe remains a T055 validation gate.
The local upgrade checks use 0.1.0 and a private 0.1.1 candidate built from a source copy with only the version changed.
The systemd service and container both report the new version after replacement.
The checks use the same compatible archive format. They do not prove a future format migration.
The verified OCI manifest digest is `sha256:fca9af1a6f144684c9324f969dc2fc29fd79537c66c6cc48de9928e04bbe9d7e`.
The retained local image ID is `sha256:974bfadfa78884292a47bb1ab886ab4d0ffc8beabb115d3e08eccbd8f3240228`.
This is a private test image identity, not a published registry reference.
