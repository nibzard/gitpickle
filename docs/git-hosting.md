# Git HTTPS hosting

Git uses Hypertext Transfer Protocol Secure (HTTPS) for client access.
Hosting is off by default. Backups do not require an inbound endpoint.
Explicit enablement exposes protected mirrors. Direct pushes never update them.
Explicit hosted mode permits durable client pushes as described below.
The canonical remote uses the immutable repository ID, for example `github-123`.
Source names do not control authorization or routing.

## Enable hosting

1. Configure the existing private policy with a `git_read` grant for each reader and repository ID.
   Use an expiry in Unix seconds. A `status` grant does not grant Git access.
2. Supply reader credentials through `GITPICKLE_MANAGEMENT_TOKENS`, using the existing runtime identity format.
   Keep credentials out of the policy, URLs, shell arguments, and durable records.
3. Start the daemon with the existing state and policy options.
   Add `--enable-git --git-base-url https://git.example.com/git`.
   Both options are required. The listener remains on IPv4 loopback.
4. Terminate Transport Layer Security (TLS) at a trusted local reverse proxy.
   Forward `/git/` to the daemon. Preserve `Authorization`, `Git-Protocol`, and request bodies.
   Keep `/v1/management` private. Do not trust network access as repository authorization.
5. Configure a Git credential helper. Supply a nonempty username and the runtime token as the password.
   The token selects the principal. The username does not select an actor.
   Do not put the token in a remote URL. Require a certificate trusted by the client.
6. Run `gitpickle hosting --repository github-123 --server http://127.0.0.1:PORT`.
   The result displays `protected_mirror`, `read_only`, and the copyable `remote_url`.
   Use `--json` for the versioned result.
7. Run ordinary Git commands with that URL:

   ```sh
   git clone https://git.example.com/git/github-123.git
   git fetch origin
   git pull --ff-only
   ```

The policy grant uses the existing schema:

```json
{"actor":"reader","permission":"git_read","repositories":["github-123"],"expires_at":1900000000}
```

Use Tailscale Serve or the deployment proxy for HTTPS. The daemon does not provision certificates or enable public exposure.
Configure a proxy timeout above 130 seconds for Git negotiation, with additional time for archive reconstruction.
The management timeout and access rules must remain separate.
Final VM, container, and tailnet deployment acceptance remains T055.
Local certificate tests do not establish those deployment results.

## Publication and authorization

Each request authenticates and checks the current repository grant before reading archive data.
Missing or invalid credentials return HTTP 401 with a Basic challenge. A missing grant returns HTTP 403.
Grant expiry and replacement apply to subsequent requests. Existing transfers can finish.
Request logs contain no paths, credentials, or bodies.

The publication chain selects the newest committed generation. Unpublished points and staging refs remain invisible.
The gateway reconstructs a fresh bare repository and verifies object integrity before serving it.
It never loads source configuration, hooks, templates, or credential helpers.
The Rust `GitEngine` port delegates negotiation and pack creation to native `git http-backend`.
The gateway does not implement Git packet negotiation.
A missing, corrupt, or incomplete latest generation returns HTTP 503. It does not silently serve an older generation.
This includes required Git Large File Storage (LFS) and enabled wiki coverage.

LFS basic downloads use the same credentials and repository grants.
Only objects listed in the selected committed point are available. Size and digest validation precede transfer.
Protected mirrors reject upload batches and Git receive-pack requests with HTTP 403 and a read-only explanation.
Unknown LFS objects return an object error, or HTTP 404 for direct downloads.
No request changes source refs, archive publications, or local remotes.

The filesystem archive adapter serves its committed data.
The cloud adapter downloads durable data at startup and publishes remote data before local visibility.
The gateway uses this validated local archive cache. It does not expose capture staging as a remote.
This implementation reconstructs each request. It has no persistent hosting cache to trust after restart.

## Tested compatibility

Local evidence: `tools/test_gitpickle_hosting.py`, 15 October 2026.
`cargo xtask check` passed, including Rust tests and 239 Python tests.
The focused suite includes transfers above 64 MiB for both Git and LFS.
Clients: Git 2.47.1 and Git LFS 3.4.1 on Linux.
The tests use ordinary clients, a trusted disposable certificate, free loopback ports, and the installed executable.
These results apply to bounded local fixtures. They do not establish compatibility with every client version or deployment.

| Feature | Result and scope |
| --- | --- |
| Clone, fetch, pull | Passed over HTTPS; committed updates become visible after publication |
| Branches and tags | Default branch and lightweight tag preserved |
| Authentication | Basic challenge and Git credential helper passed; invalid and missing credentials denied |
| Authorization | Repository denial, grant revocation, and path traversal checks passed |
| Protected mirror pushes | Normal and force-with-lease pushes rejected with an explanation |
| Empty repository | Clone preserves the unborn default branch |
| Protocol versions | Versions 0, 1, and 2 passed clone and shallow-history tests |
| Advertised shallow history | Depth-one clone and unshallow fetch passed |
| Protocol version 2 | Native advertisement, reference listing, and fetch exercised by real clients |
| Compressed request | Gzip reference listing passed; excess expansion rejected |
| Partial-clone filters | Disabled and not advertised; no filtered-clone support claim |
| Object formats | SHA-1 and SHA-256 clone passed |
| LFS downloads | Real `git lfs pull`, batch, and authenticated object download passed after source payload removal |
| Incomplete or lost LFS | Latest generation unavailable; no false successful download |
| LFS uploads and locking | Basic uploads require hosted mode and `git_write`; locking endpoints absent |
| Hosted pushes | Native receive-pack with durable archive publication; see hosted mode below |
| Signed pushes | No push certificate support advertised |
| Signed commits and tags | No signature verification or trust-policy claim |
| SSH transport, dumb HTTP, server hooks | Unsupported |

Requests are limited to 1 MiB after decompression. LFS batches accept at most 100 objects.
Git responses and LFS objects use file-backed streams with bounded memory.
Python copies payloads in 64 KiB chunks. The Rust engine writes to a caller-supplied stream.
The bridge retains no complete response in memory. Backend headers have a 64 KiB limit.
Archive reconstruction also streams pack input and LFS data through verified temporary files.
Size and SHA-256 checks finish before a payload becomes available to the client.
Git backend completion is checked before HTTP headers are sent.
Temporary files close on success or failure. A disconnected client stops the response copy.

This design stages data on disk before transfer. Allow disk space for reconstruction and temporary copies.
The backend has a 120-second deadline and a 16 GiB output limit.
The shared runner also limits child resources and individual generated files to 16 GiB.
LFS downloads have no 64 MiB limit; the committed manifest supplies the exact size.
Errors before transfer return a redacted response. Errors during transfer close the connection.

Local regression fixtures exercise an incompressible Git pack above 64 MiB and a 65 MiB LFS object.
Real HTTPS clients verify Git integrity and the LFS digest after source payload removal.
Authentication denial and corrupt LFS data remain covered.
These tests are correctness evidence. Full-size performance acceptance remains T051.
Capture writes native pack files to disk with a 32 MiB target per pack.
A single Git object can exceed that target. Initial captures above 64 MiB now pass, including a 65 MiB blob.
Each pack uses a conditional single write with a 5 GiB upper limit.
The storage bridge still buffers individual packs; full-size memory acceptance remains T051.

Run the focused suite with:

```sh
python3 -B -m unittest discover -s tools -p test_gitpickle_hosting.py -v
```

The suite builds the locked executable. It requires Python 3.10 or later, Git, Git LFS, OpenSSL, and `prlimit`.
Fixtures, certificates, keys, clones, and generated reports belong in ignored directories.
Temporary resource names use `LOOPER_RESOURCE_PREFIX` when supplied.

The [publication boundary probe](git-publication.md) proves a bounded native Git response gate.
The hosted coordinator uses its packet gate. T037 owns full writable compatibility acceptance.

## Explicit hosted mode

T036 adds writable hosting through the archive publication coordinator.
Hosting stays disabled until `--enable-git` is set. Protected mirrors reject pushes.
The `git_read`, `git_write`, and `hosting_manage` grants have separate purposes.
Only `hosting_manage` permits creation, conversion, or resuming source updates.
All grants name exact repository IDs and expire through the existing policy service.

Inspect the current publication before a mode change:

```sh
gitpickle hosting --repository r --json
gitpickle hosting --repository r --action convert --expected-head repos/r/commits/DIGEST
gitpickle hosting --repository new --action create
```

Use the exact `head` from the status response. A stale head fails with `conflict`.
Conversion requires complete Git, LFS, and enabled wiki coverage.
Conversion retains that publication as its checkpoint. All previous points remain retained.
Creation publishes an empty repository with an unborn `main` branch.
Durable mode records live under `execution/hosting/REPOSITORY` and survive cache loss.
Source capture checks the mode under the same repository lock used by pushes.
Queued source captures cannot overwrite hosted refs after conversion.

Resuming source updates requires an explicit reconciliation decision:

```sh
gitpickle hosting --repository r --action resume --expected-head repos/r/commits/DIGEST
```

Inspect and reconcile the current hosted refs with your source first.
This command records your decision against the exact current publication.
It permits the next source capture to replace current refs. Retained hosted points remain recoverable.
No automatic merge or two-way synchronization occurs.
The management operation is `hosting_change`, with `repository`, `action`, and `expected_head` fields.
Creation uses a null expected head. Conversion and resumption require the exact publication key.

Pushes use native `receive-pack` against a private restored generation.
The shared outgoing packet gate retains the native final report and flush.
The archive persists the resulting closure and publication before HTTP success leaves the service.
Readers select only committed archive generations. Source configuration and hooks never enter staging.
Normal partial results and requested atomic behavior come from native Git.
Source and push mutations share the writer's repository lock.
Final reports are retained under `execution/pushes` before publication.
An identical request can replay its report after a lost response if that generation remains current.
A later generation requires fresh ref negotiation. Ordinary retries discover committed refs through advertisement.
Storage failures return an HTTP error without releasing native success.
A failure after publication can leave committed data despite a client error.

LFS basic uploads require `git_write` and hosted mode.
A successful PUT means its digest-checked bytes reached the durable archive adapter.
A push rejects the entire staged result if any required historical LFS object is absent or corrupt.
Upload LFS content first, then retry the Git push. Incomplete hosted generations are never published.
Unused uploaded objects remain retained; automatic cleanup is disabled.
Uploads and Git requests currently have a 64 MiB limit. Downloads retain the streaming behavior above.
The receive process has a 120-second deadline. Progress is buffered in this integration.
The full provider crash matrix, compatibility scope, and latency acceptance remain T037 and T054.

## T036 local validation

Local acceptance covers authorized creation, retained conversion checkpoints, stale reconciliation,
paused scheduling, queued source jobs, private staging, missing LFS rejection, and durable LFS uploads.
Native HTTPS clients push and clone through the gateway. A real Git LFS client uploads through its basic transfer endpoint.
Fault tests reject payload storage failure and replay a committed request after a lost final response.
The cloud composition test deletes the entire cache and source, then reconstructs hosted refs from remote adapter bytes.
This test uses an in-memory provider fixture and does not claim live provider acceptance.
The Rust workspace tests, deployment regressions, publication probe, and four bounded models also pass.
The model runner writes reports under ignored `spec/results/` paths.
