# Guided installation

Run `gitpickle setup` for keyboard prompts. You can also start with `gitpickle protect github` without a configured server.
Choose `local`, `ssh`, or `coolify`. Use `--configure` to generate storage credentials and GitHub policy through guided prompts.
The first-run protection entry point selects this mode automatically.
Backups require outbound access to GitHub and storage. They require no inbound HTTPS endpoint.

Setup saves a nonsecret plan. Review the target, dependencies, privileges, ports, files, and binary checksum.
Installation requires a second invocation with `--resume --apply` and confirmation.
Automation also requires `--yes`. Noninteractive calls never prompt. Use `--json` for a versioned result.
The plan contains no credentials. Existing Git, SSH, MCP, Docker, Coolify, and runtime configuration remains unchanged.
Use `deploy/generated/` for generated plans inside a checkout. Git ignores this directory.
Use a private directory outside the checkout for runtime secrets.

## Runtime configuration without editing files

Add `--configure` to setup. Supply an existing bucket; setup does not create buckets.
Choose R2, S3, or B2 and enter the HTTPS endpoint, region, bucket, and dedicated archive prefix.
For automation, use `--provider`, `--endpoint`, `--region`, `--bucket`, and `--prefix`.
Use region `auto` for R2. Use the bucket's region for S3 or B2.

Credentials use masked prompts. Automation supplies `GITPICKLE_GITHUB_TOKEN`,
`GITPICKLE_STORAGE_ACCESS_KEY`, `GITPICKLE_STORAGE_SECRET_KEY`, and `GITPICKLE_TOKEN`
through the environment. The management token requires at least 32 random characters.
Keep that token in your credential provider for subsequent client commands.
No secret command-line arguments are supported. Noninteractive calls never prompt.

Preview reads the authorized GitHub inventory. It stages secrets in `runtime-inputs.json`
with mode `0600` inside the private output directory. Treat this file as a credential file.
The separate `plan.json` and terminal output contain no credentials.
The policy grants the human owner access to current and future GitHub repositories for one year.
The setup review lists `authorized_sources: ["github"]` and the current repository IDs.
This source grant covers protection, capture, status, and recovery. It grants no Git hosting permissions.
Future capture starts only after you apply protection with `--all --include-future`.

Existing policies with exact repository IDs keep their restricted scope.
To enable future repositories, add `"sources": ["github"]` to the owner's reviewed grants
for `protect`, `backup`, `sync`, `status`, `plan_restore`, and `apply_plan`.
Keep the existing repository IDs and expiry. Increase the policy revision and restart the daemon.
Review and apply a protection plan with `--all --include-future`.
Setup does not overwrite an existing policy or upgrade its permissions automatically.

Apply requires `--resume --apply` and confirmation; automation also requires `--yes`.
It rechecks repository scope before installation. The target checks storage listing access,
publication capabilities, and 2 GiB of available staging disk before writing runtime files.
Protection later performs its isolated storage write/readback probe before accepting jobs.
Setup never claims that configured repositories are verified or fresh.
It starts no service and submits no backup jobs. Start the service and run `protect github`
to review and authorize ongoing protection through the shared workflow service.

Resume reuses staged credentials and stable server identity. A completed apply needs no repeated confirmation.
Changed storage, repository scope, or existing different runtime files require a new review.
Existing runtime files are never overwritten. Preserve the private staged files until setup completes.
For Coolify, setup generates the runtime files for the host administrator to mount;
target validation occurs when protection runs through the deployed service.

## This machine

Obtain and verify the Linux binary as described in [deployment.md](deployment.md).
The supported package targets Linux x86-64 and glibc 2.36 or later.
Setup checks Python 3.11+, Git, Git LFS, `prlimit`, `systemctl`, and the system certificate bundle.
An administrator installs missing packages. Setup never runs `sudo`, a package manager, or downloaded scripts.

1. Run `gitpickle setup --target local --configure --binary /verified/gitpickle --port 8080 --output deploy/generated/local`.
2. Read `deploy/generated/local/plan.json`. Check the binary checksum against your authenticated release evidence.
3. Enter the storage location and credentials through the prompts.
4. Review the discovered repository scope, storage location, and owner grants.
5. Repeat the command with `--configure --resume --apply`. Confirm installation and configuration.
6. Start the service with `systemctl --user enable --now gitpickle`. Check `journalctl --user -u gitpickle` and `/readyz`.
7. Run `gitpickle protect github --server http://127.0.0.1:8080` to preview and approve protection separately.

Choose a free port before installation. Setup does not reserve a port or stop another listener.
The service uses `~/.local/share/gitpickle/releases/SHA256/gitpickle` and a systemd user unit.
The archive uses the systemd user state directory. Setup does not start or enable the service.
The user manager must support the supplied hardening directives. Ask your administrator about lingering for operation after logout.
No administrator permission is inferred from a setup confirmation.

Retry the same command with `--resume` after interruption. An installed binary and unit are reused without duplication.
Apply rechecks the target account, machine identity, dependencies, binary, and plan.
A changed plan requires review in a new output directory. Missing dependencies also require a new preview after installation.
An existing, different service is a conflict. Use the explicit stop-and-upgrade procedure in [deployment.md](deployment.md).
Setup never overwrites runtime files, replaces an existing different unit, or starts a second archive writer.

## A Linux server over Secure Shell

Secure Shell (SSH) protects installation and management traffic. It does not provide Git SSH hosting.
Use a dedicated unprivileged account and a working systemd user manager on the server.

1. Obtain the server's SSH host-key fingerprint through an independent trusted channel, such as the server console.
2. Verify the key and place it in a dedicated `known_hosts` file. `ssh-keyscan` alone does not authenticate a server.
3. Run `gitpickle setup --target ssh --host backup@server.example --known-hosts /private/known_hosts --binary /verified/gitpickle --port 8080 --output deploy/generated/server`.
4. Inspect the plan, including the machine identity, account, dependency checks, and exact service unit.
5. Repeat that command with `--resume --apply` and confirm. Use `--ssh-port PORT` when needed.
6. Use `--configure` during preview and apply to generate runtime files. Start the service as described above.
7. Open a loopback tunnel, then run protection through it: `ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 backup@server.example`.

Use `gitpickle protect github --server http://127.0.0.1:18080` with that tunnel.
Use a free client port and retain host-key verification for your tunnel command.

Setup requires an existing SSH credential, such as an authorized key in your agent. It never requests a password.
It uses strict host-key checking and the specified key file. It disables ambient SSH configuration, forwarding, and connection sharing.
Unknown or changed keys fail before probing or installing. The probe reads only host facts and minimal Tailscale status.
The binary travels through the authenticated SSH channel. The target checks its SHA-256 checksum before writing it.
No system-wide SSH configuration or account authorization changes occur.

## Guided Coolify deployment

Coolify setup generates a recipe. It does not call the Coolify API, create a project, enroll a host, or deploy a service.
The host administrator must already provide Docker and Coolify.

1. Run `gitpickle setup --target coolify --image registry.example/gitpickle@sha256:DIGEST --port 8080 --output deploy/generated/coolify` with the verified 64-character digest.
2. Review `plan.json` and `compose.yaml`. The host port binds to `127.0.0.1`.
3. Add a Docker Compose service in Coolify. Paste the generated recipe. Keep **Domains** empty for private operation.
4. Provide `GITPICKLE_RUNTIME_ENV` and `GITPICKLE_POLICY` as absolute private host paths. Use `--configure` to generate both files in the private output directory. Set their host ownership to UID 10001.
5. Review **Show Deployable Compose**. Confirm the loopback mapping, UID 10001, mounts, image digest, and health check.
6. Keep one replica and stop the old writer before replacement. Deploy and inspect readiness and logs.
7. Use a loopback SSH tunnel for management. Configure host Tailscale Serve only if you enable Git hosting.

Runtime file ownership, mode, sandbox, storage credentials, and recovery keys follow the same deployment contract.
Do not put credentials in the recipe or plan. The pinned image preserves its command and health check.
Do not expose the container port on all host interfaces. Do not assign a generated public domain.
Resume recipe generation with the same command and `--resume`. It preserves an unchanged recipe exactly.
Inspect the final Coolify definition because domain and network settings can change its exposure.
See [Coolify Compose configuration](https://coolify.io/docs/services/configuration/docker-compose) and
[Coolify networking](https://coolify.io/docs/services/configuration/networking).

## Private HTTPS with Tailscale

Setup detects an existing Tailscale executable and reads its connection state and DNS name on the selected Linux host.
For Coolify, inspect the host directly. Installing Tailscale inside the application container is unnecessary.
If Tailscale is absent, stopped, or needs login, an authorized operator completes that prerequisite separately.
Setup never runs `tailscale up`, changes account enrollment, enables HTTPS, or enables public Funnel.

1. Confirm the existing tailnet permits your intended clients. Enable MagicDNS and HTTPS certificates through your authorized tailnet controls.
2. Inspect `tailscale status` and `tailscale serve status --json` on the host. Preserve existing routes and settings.
3. Enable Git hosting with `--enable-git --git-base-url https://HOST.TAILNET.ts.net/git` in the daemon arguments.
4. Keep the listener on loopback. For containers, retain `--host 0.0.0.0 --container-network` inside the container and the host loopback mapping.
5. Review and run `tailscale serve --bg --https=443 --set-path=/git http://127.0.0.1:8080/git` on the host.
6. From an allowed tailnet client, verify HTTPS and authenticated Git fetch. Check that management paths are unavailable through this route.
7. Inspect Serve status again. Confirm there is no public Funnel and no unintended route.

For containers, preserve all image command arguments when adding the two hosting arguments.
Use the generated plan's port instead of `8080` if you select another port.
Do not replace an existing `/git` route without reviewing its owner and effect. Setup never resets Serve configuration.
The `/git` mount limits the proxy to Git and Git Large File Storage (LFS) paths.
Management remains available through the SSH tunnel. Tailnet membership does not replace repository authentication and grants.
Enabling certificates can publish the node's DNS name in certificate transparency logs. Review that name first.
See [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve) and
[Tailscale HTTPS prerequisites](https://tailscale.com/kb/1153/enabling-https).

## Optional public HTTPS

Public Git hosting requires explicit opt-in, an authenticated Git endpoint, a domain, and a trusted TLS proxy.
Set the daemon's canonical base URL to `https://git.example.com/git`. Keep management behind the SSH tunnel.
Expose only `/git/` at the public proxy. Block `/v1/management`, health routes, and all other paths.
Never forward backup-source credentials to clients. Preserve the client's Git authorization header.

The [Nginx example](../deploy/nginx-git.conf.example) streams request and response bodies at the proxy.
It disables proxy buffering, permits large request bodies, and extends transfer timeouts.
See the [Nginx proxy directives](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) and
[body limits and timeouts](https://nginx.org/en/docs/http/ngx_http_core_module.html).
Configure certificates before use. The proxy host needs access to the daemon's loopback port.
In Coolify, use an equivalent explicit path rule and buffering, size, and timeout settings.
Do not use a default domain route that forwards every path. Review generated proxy labels before deployment.
For a container proxy, host loopback refers to that container. Configure a private backend route deliberately; do not publish the daemon broadly.

Proxy streaming does not remove application limits. Git downloads use file-backed streams with a 16 GiB backend limit.
Hosted push requests and LFS uploads have a 64 MiB limit.
See [Git hosting compatibility](git-hosting.md) for deadlines, staging disk, and transfer limits.

## Validation scope

Local tests exercise installation replay, interrupted reload, changed plans, SSH rejection, recipe isolation, and runtime-file preservation.
They use temporary home directories and a fake user manager. They do not enroll a tailnet or change a live service.
Run `python3 -B -m unittest discover -s tools -p 'test_gitpickle_setup.py'`.
The compiled CLI tests also exercise recipe generation outside the source tree.
Fresh VM, actual SSH deployment, Coolify routing, private HTTPS, and allowed-client checks remain in T055.
