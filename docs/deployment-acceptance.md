# Deployment and recovery acceptance

T029 covers this development machine. T055 requires fresh hosts through AgentLab on `pve2` after T052.
A local result cannot close a fresh-host, Coolify, private HTTPS, or tailnet gate.

## Repeat the local checks

Use the authorized R2 runtime environment and the three `LOOPER_RESOURCE_*` variables.
Keep credentials outside the checkout. The checker validates the authorized development bucket and base prefix.
Use a unique prefix for each allocation. Keep cleanup registered by earlier setup.

1. Run `python3 -B tools/gitpickle_package.py`, then verify `dist/SHA256SUMS` from `dist/`.
2. Run `GITPICKLE_TEST_BINARY="$PWD/dist/gitpickle" python3 -B -m unittest discover -s tools -p 'test_gitpickle_deployment.py'`.
3. Run `python3 -B tools/gitpickle_deployment_acceptance.py --binary dist/gitpickle`.
4. Run `docker build --network host --iidfile .cache/deployment-image-id -f deploy/Dockerfile .`.
5. Run `python3 -B tools/gitpickle_deployment_acceptance.py --image "$(cat .cache/deployment-image-id)"`.
6. If bridge DNS fails on this development host, repeat step 5 with `--host-network`. Retain the failed bridge evidence.
7. Run `python3 -B tools/gitpickle_systemd_acceptance.py` to check the installed user unit separately.

The build pins the toolchain, lockfile, base images, and Debian snapshot.
The checker resolves the image to its immutable local identifier. The report also records the release binary checksum.
Each run creates an isolated R2 prefix, journals uploaded digests, and registers cleanup before remote writes.
Container and volume labels include the Looper prefix and artifact directory.
Binary cleanup checks the user, exact command, state path, and process group before stopping a process.
Cleanup preserves the shared bucket, unrelated containers, and existing service units.

The fixture contains one fixed commit on `main` and a 26-byte file named `data`.
Its repository identifier is `r`. Only `r` receives a read grant; `denied` receives none.
The checker removes the source and seed cache before starting the first deployment.
It checks startup, restart, replacement, and recovery into empty application state.
Each stage checks ordinary clone, three fetches, exact HEAD, file content, and `git fsck --full --strict`.
Anonymous and invalid tokens return 401. A valid token without a repository grant returns 403.
Protected mirror pushes fail. All 13 immutable archive and job records must match their seed digests.
The container also runs the recovery CLI and checks the restored file.

Reports, fixture identifiers, and daemon logs remain under `LOOPER_ARTIFACT_DIR/deployment-*/`.
Registered cleanup uses retained programs outside the checkout. Remote removal checks every journaled object's digest.
The checker verifies that tracked and unignored project files retain their contents and modes.
Generated checkout output belongs under ignored `dist/`, `.cache/`, or `tests/results/deployment/`.

## Local observations on 15 October 2026

The first integrated run uses Linux 6.8.0-147-generic, glibc 2.39, and Git 2.47.1.
The host is shared. Hardware and allocation limitations are in [local-test-environment.md](local-test-environment.md).
The release binary SHA-256 is `075452aa3a0410cb2035f49e8a4d051ece91ba24363f6d2a236591b86b3e5a88`.
The container image is `sha256:a63a7b7ab28d06e994561dbddc4f02f8606371fb32afe9ee3443e10c0ba14fe5`.
These are private test artifacts, not published releases.

| Measurement, seconds | Binary daemon | Container with host networking |
| --- | --- | --- |
| Initial readiness | 2.522 | 2.803 |
| Restart readiness | 1.754 | 1.948 |
| Replacement readiness | 1.755 | 2.264 |
| Empty-state readiness | 1.754 | 1.994 |
| Initial clone | 0.246188 | 0.245023 |
| Clone after empty-state recovery | 0.236323 | 0.225080 |
| Warm fetch range, 12 samples | 0.146954–0.164495 | 0.131481–0.145498 |

Readiness starts before process or container launch and ends at the first successful readiness probe.
Container health confirmation takes longer: initial and empty-volume checks take 5.428 and 5.426 seconds.
The user-unit lifecycle check passes installation, restart, same-version replacement, and graceful termination.
That systemd check uses a local archive. It does not establish integrated systemd/R2 or remote Git acceptance.
The binary and container integrated checks both reconstruct 13 records from actual R2 storage.

The default Docker bridge fails during archive reconstruction. A separate DNS probe returns `EAI_AGAIN` for the R2 hostname.
Host networking with free loopback ports passes. Shared DNS and Docker configuration remain unchanged.
The standard bridge deployment remains a required T055 check.

These are bounded observations, with one readiness sample per stage and three warm fetch samples per stage.
They are not a regression baseline, a percentile estimate, or a scale result.
Cold means empty application state. Operating system and provider caches remain uncontrolled.
The HTTPS fixture uses a locally trusted test certificate and a bounded loopback proxy.
It does not test Tailscale certificates, account enrollment, external routing, or the production proxy recipe.
Each Git request currently reconstructs a temporary generation. Warm means repeated client access, not a retained server repository cache.
Replacement uses the same release unless a different compatible `--upgrade-image` is supplied.
This run does not test a version upgrade or an archive format migration.
No optional archive encryption is enabled; no client recovery key is needed for this fixture.
Git Large File Storage and wiki coverage remain supported by the separate T022 evidence, not this tiny fixture.

## Fixtures for fresh-host acceptance

Generate the same fixed Git history in an isolated, owned directory:

```sh
mkdir -p fixtures/generated/deployment
python3 -B tools/gitpickle_deployment_probe.py fixture \
  --directory "fixtures/generated/deployment/${LOOPER_RESOURCE_PREFIX}-source"
```

Retain the printed HEAD and capture this repository as `r` into the authorized disposable archive.
Use the seed and journal setup in `gitpickle_deployment_acceptance.py` for direct local capture into R2.
Register cleanup before capture. Keep the journal and receipt independently from the host and its cache.
Use the same server identity after restart. Stop and fence the old writer before a different host takes ownership.
Keep policy, runtime secrets, and required recovery keys outside the removable state volume.
Use a runtime reader token and a `git_read` grant for `r` only. Do not grant `denied`.
The hardcoded local test token is only for the loopback fixture; use a fresh token on remote deployments.

After deploying, run this client probe from an authorized machine. Supply its token through the named environment variable.
The probe uses ordinary Git and Python. It does not require the GitPickle executable on the client.

```sh
python3 -B tools/gitpickle_deployment_probe.py client \
  --url https://HOST.TAILNET.ts.net/git --expected-head RECORDED_HEAD \
  --output tests/results/deployment/tailnet-client.json
```

The system trust store validates the certificate. Use `--ca /private/ca.pem` only for an authorized private certificate authority.
Do not disable certificate validation. Store raw reports outside the source repository for final evidence.

## Required profile procedure for T055

Use [deployment.md](deployment.md) and [setup.md](setup.md) for exact installation commands and security settings.
For each profile, record the artifact digest, host identifier, runtime dependencies, hardware, network, and independent evidence location.
Record every unrun scenario as pending. Preserve the distinction between a local fixture and actual client routing.

1. Provision a fresh, isolated `pve2` virtual machine through configured AgentLab access. Register owned cleanup before provisioning.
2. Install the verified binary with the supplied systemd user unit. Configure private runtime files and a free loopback port.
3. Repeat on a fresh Coolify/container profile using the verified registry digest, one writer, loopback publication, and separate secrets.
4. Configure host Tailscale Serve for `/git` with the canonical HTTPS URL. Preserve unrelated routes and keep management private.
5. Run the client probe from an allowed tailnet client. Check anonymous access, invalid credentials, denied repositories, and rejected pushes.
6. Stop the writer, delete only owned test cache/index state, remove source access, and restart from R2 and required recovery keys.
7. Repeat the client probe and independent restore. Compare refs, content, durable job records, and digests; retain raw timings by profile.

For step 3, check **Show Deployable Compose**, the UID, mounts, health check, and actual host port exposure.
For step 4, verify the trusted certificate and inspect Serve status before and after configuration.
From a client denied by the tailnet policy, confirm the endpoint is unreachable. Network permission does not replace repository grants.
Confirm `/v1/management` is unavailable through the HTTPS proxy. Confirm public Funnel remains disabled.
Use `git ls-remote`, clone, fetch, and strict integrity checks from each allowed client profile.
Record startup and restart readiness separately from TLS connection latency, clone time, fetch time, and restore duration.
Collect warm repetitions and repeated empty-state starts. Record cache definitions and sampling limits.
Use an independent check of GitHub blocking for the clean-machine drill, as in T022.
Do not infer this check from successful R2 access alone.

Fresh systemd/R2 recovery, Coolify, standard bridge networking, private HTTPS, and tailnet client results remain pending in T055.
