# Guided command-line workflow

`gitpickle help` shows the normal commands. `gitpickle help --all` shows automation and service options.
The executable embeds the shared Python services. Install Python 3.10 or later and Git on the execution host.
Install Git Large File Storage (LFS) to capture required LFS data.

## Configure the execution host

Run the daemon as an unprivileged user. Use a private archive directory and a private policy file.
The daemon initializes an empty directory. Existing archives retain their writer identity and takeover rules.
Use [guided setup](setup.md) for local installation, SSH installation, and private Coolify deployment.
Without a configured daemon, interactive `protect github` opens that setup guide.

The policy schema remains the schema in [security.md](security.md).
Each grant contains an actor, a permission, exact repository IDs, and an expiry in Unix seconds.
Use `github-123` for GitHub repository ID 123. Human credentials must specify `"agent": false`.
Protection uses `protect`, `backup`, `sync`, and `status` grants.
Restore uses `plan_restore` and `apply_plan` grants. The human actor must also appear in `approvers`.
Changing protection requires permission for repositories removed from the old policy.
Agents cannot use the human workflow or grant approval through a request boolean.

Supply credentials through the runtime environment or a service credential provider:

- `GITPICKLE_MANAGEMENT_TOKENS`: the daemon's map of tokens to authenticated identities.
- `GITPICKLE_GITHUB_TOKEN`: the daemon's GitHub credential. It never enters client arguments or archive records.
- `GITPICKLE_MANAGEMENT_TOKEN`: the client's management credential. Interactive entry uses a masked prompt.
- `GITPICKLE_STORAGE_ACCESS_KEY`, `GITPICKLE_STORAGE_SECRET_KEY`, and optional `GITPICKLE_STORAGE_SESSION_TOKEN`: provider credentials.

Start the service with an explicit staging budget. The listener selects a free port when you use `--port 0`.
Its startup JSON contains the selected loopback address. Use that address as `GITPICKLE_SERVER` on the client.

```sh
gitpickle serve --state /private/archive --policy /private/policy.json --port 0 --staging-bytes 1073741824
```

The staging budget reserves space per job. It is an operator budget, not a measured repository size.
Admission also preserves 256 MiB of free disk space. Transfer charges and unknown LFS sizes remain unknown.

## Select storage

For cloud storage, supply nonsecret `GITPICKLE_STORAGE_CONFIG` to the daemon:

```json
{"provider":"r2","endpoint":"https://ACCOUNT.r2.cloudflarestorage.com","region":"auto","bucket":"existing-private-bucket","prefix":"gitpickle/"}
```

Use `s3` with its endpoint and region for S3. Credentials must permit listing, reading, and conditional creation within the prefix.
The writer sends dependencies before points and publication records. Every upload verifies remote bytes before local visibility.
Durable job acceptance and completion use the same boundary. Failed remote writes cannot succeed from cached bytes.
A fresh private cache downloads archive records before opening the writer. Conflicting local records stop startup.
Migration from a filesystem archive requires a separate explicit procedure. Setup does not silently publish existing local data.

The current B2 adapter lacks conditional creation. Protection rejects that capability before creating an archive.
Read-only B2 recovery remains available. The existing finding `b2-durable-archive-publication` tracks the missing write capability.
The helper uses single-object PUT operations. Provider object-size limits can reject large objects without publishing a complete point.
Live R2 verification belongs to T022. Live S3 and B2 verification belongs to T054.
Deterministic adapter and workflow tests do not establish live provider behavior.

Without cloud configuration, the service uses its filesystem archive. The plan identifies that mode.
A filesystem archive alone does not establish the project's independent object-storage protection guarantee.

Probes use isolated `.probes/` keys under the configured prefix. They never replace archive data.
The storage port has no delete operation. Probe objects remain until the operator removes that isolated prefix.
No command creates a bucket. Create a bucket through a separately authorized provider operation, then configure its restricted credentials.

## Protect and resume

1. Run `gitpickle protect github --all --preview` to inspect authorized scope and preflight checks.
2. Review storage, scope additions, stopped protection, staging requirements, and the plan expiry in UTC.
3. Run `gitpickle protect github --resume REFERENCE` to review and apply the saved plan.
4. Run `gitpickle status` to inspect coverage. Use `gitpickle doctor --all` to inspect configuration and recovery evidence.

Add `--include-future` only when you intend daily discovery to expand protection.
The policy captures every six hours, discovers daily, keeps retained points, and leaves hosting off.
New repository IDs still require matching execution grants. Discovery never grants authority.
The client describes only repositories exposed by the credential as authorized repositories.

Inside a GitHub checkout, `gitpickle protect` resolves `origin` to a stable GitHub ID.
It never writes Git configuration, adds hooks, replaces remotes, or uploads the working tree.
Uncommitted work and unpushed branches remain outside the protected scope.

Setup stores nonsecret plans and completion records in the archive.
Resume returns completed setup without repeating GitHub authorization or accepting duplicate initial jobs.
A policy or inventory change invalidates an uncompleted plan. An expired plan needs a new preview.
No setup path creates a bucket, including when a request contains an approval boolean.

## Capture and follow jobs

```sh
gitpickle backup --repository github-123 --key capture-001 --preview
gitpickle backup --repository github-123 --key capture-001 --yes --json --wait
gitpickle sync --all --key source-check-001 --yes --json
gitpickle jobs JOB_ID --json --wait
```

`backup` captures now. `sync` records source state without publishing a recovery point.
Both commands require an explicit repository, a recognized checkout, or `--all`.
A changed directory never expands a single-repository write to all repositories.
Previewed scope binds approval. A changed scope requires a new plan.
Reuse the same key to retry an ambiguous submission. Reuse with a different scope returns `conflict`.
The batch scope and each job acceptance survive loss of the local database.

A job ID appears only after durable acceptance. Diagnostics print it before following work.
`--wait` follows the server's jobs. Ctrl-C detaches the client and reports that server work continues.
It does not request cancellation. Use the printed ID to inspect the job later.

## Restore safely

```sh
gitpickle restore github-123 --destination ./recovered --preview
gitpickle restore github-123 --destination ./recovered --yes --json --wait
gitpickle jobs RESTORE_JOB_ID --restore --json
gitpickle restore --from-storage /archive --repository github-123 --destination ./recovered --json
```

Interactive `restore` offers the authorized inventory and a new destination.
The shared service selects the newest complete, verified point. Existing destinations fail before approval.
Restore approval binds the actor, destination, point, scope, policy revision, impact, and expiry.
The daemon owns accepted restore execution. A client disconnect does not stop it.
An interrupted daemon can leave an unknown restore outcome. Inspect the destination before requesting another restore.
Offline recovery uses archive access and required keys. It does not require the daemon or GitHub.
Use `--from-storage --provider r2|s3|b2` with the connection options in advanced help.

## Automation and accessibility

Noninteractive calls never prompt. Supply a scope, runtime credentials, and `--yes` for management writes.
Automation supplies a request key for `backup` and `sync`.
`--json` writes one versioned result to stdout. Plans, progress, and diagnostics use stderr.
The result uses `schema_version: 1`, scoped repository IDs, stable errors, and Unix seconds in UTC.
A successful status query can report degraded protection. `doctor` and `status --check` return 6 for degraded protection.

Prompts use the keyboard. Output uses status words and plain text, with no ANSI sequences.
`NO_COLOR` needs no special terminal support. Human output wraps to the terminal width.
The client does not launch a full-screen interface.

## Local acceptance

Run `cargo xtask check`. It includes workspace checks, installed-binary contracts, and Python acceptance tests.
Workflow tests cover scope changes, `--all`, setup replay, durable IDs, explicit approval, grant revocation,
staging probes, safe restoration, narrow output, masked entry, waiting, and detachment.
Cloud tests rebuild completed jobs and Git data after deleting both the source and local cache.
Wire tests exercise signed provider requests through a free loopback port. They create no external resources.
Generated fixtures remain under ignored `fixtures/generated/workflow/` directories.
