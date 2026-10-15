# GitPickle 🥒: effortless Git protection, independently recoverable

**Product and engineering proposal · Draft 0.3 · 14 October 2026**  
**Working name:** GitPickle. It replaces GitVault from draft 0.2. Brand and naming: [section 9](#9-brand-and-naming-gitpickle).  
**Status:** Proposed design, not an existing implementation. All product commands, transcripts, budgets, and reports below are illustrative unless explicitly identified as external tooling. No performance measurements or formal verification have been performed for this proposal.

> **In a pickle? Git it back.**
>
> Protect your repositories once. Keep using Git. Recover without GitHub—or the original server.

## Decision in one page

Build a small, open-source application with one management executable operating as a **CLI, daemon, opt-in TUI, or local MCP server**. A server independently discovers and backs up authorized GitHub repositories, retains recovery points in R2, S3, or B2, and eventually serves ordinary Git clients. A laptop runs the same executable to configure protection, inspect results, and restore data.

The default experience asks users about outcomes, not infrastructure internals:

```sh
gitpickle protect github     # Guided setup and ongoing protection
gitpickle                    # What is protected? What needs attention?
gitpickle restore            # Recover safely into a new destination
gitpickle doctor             # Check configuration and recovery evidence
```

Use **progressive disclosure** to keep advanced capabilities out of the normal path, **KISS** to minimize concepts and moving parts, and **Don't Make Me Think** to make scope, outcomes, and next actions obvious. Never hide missing coverage, costs, permissions, or destructive consequences.

Internally, use a **modular monolith with domain-driven boundaries**, native Git behind an adapter, immutable archive data, and rebuildable local indexes. Apply **TLA+ to the few state transitions that can lose data**, and require reproducible benchmarks alongside implementation work—not as a final optimization phase.

**Implementation direction:** evaluate **walgit** as the preferred embedded Git Smart HTTP/object-store engine; integrate its Rust crates or maintain a narrowly scoped fork if they meet the Git compatibility, provider portability, and recoverability gates. Do not reimplement the Git wire protocol to build the management product. Its WAL is not a substitute for GitPickle's explicit, independently restorable backup snapshots. [S16]

**Recommended first release:** scheduled GitHub protection, Git/LFS recovery, bucket-only disaster recovery, concise CLI status, optional TUI, local MCP, packaged VM/Coolify deployment, Tailscale-only access, and **read-only** native Git HTTPS hosting. Add writable Git hosting only after its object-storage durability contract passes model checking, fault injection, and compatibility tests. This is a release sequence, not a removal of the full Git goal.

**Non-negotiable:** a backup reported as durably committed, or a push reported as successful, must not depend on the survival of the server's disk. Recovery is limited to captured, retained data; the product must never imply zero loss of changes it has not yet captured.

---

## Reading paths

The product experience and release decisions are visible first. Engineering detail is grouped into expandable appendices; their headings remain readable in plain Markdown viewers.

- **Product:** [promise and scope](#1-product-promise-and-scope), [user experience, TUI, and native Git](#2-user-experience), [agent experience](#3-agent-experience-ax).
- **Engineering:** [architecture, walgit adoption, deployment](#4-architecture-decisions), [formal methods](#5-tla-and-correctness), [performance](#6-benchmark-driven-development).
- **Delivery:** [milestones](#7-delivery-plan), [acceptance gates](#8-definition-of-done), then the detailed appendices.
- **Brand:** [naming decision, identity, voice, and clearance](#9-brand-and-naming-gitpickle).

## 1. Product promise and scope

### 1.1 The job to be done

A developer with hundreds of repositories should be able to say:

> “Keep my Git data independently recoverable, tell me when that stops being true, and give me a normal Git remote when I need one.”

The reference portfolio is **404 repositories**, including private repositories. That is a planning workload, not an audited inventory. The same workflow should serve one repository without looking like an enterprise backup console.

### 1.2 Five product contracts

| Contract | User-visible meaning |
|---|---|
| Independent operation | Closing the client does not stop work accepted by the server. GitHub Actions is not required to run protection. |
| Independent recovery | A fresh machine can recover from the archive, storage credentials, and any required recovery keys. No original daemon database or GitHub login is required. |
| Historical protection | Upstream deletion, loss of access, or force-push does not erase retained recovery points. |
| Native Git | Ordinary Git clients use the supported Git HTTPS endpoint; the management CLI is optional for Git operations. |
| Evidence, not reassurance | Status distinguishes captured data, verification, freshness, and actual restore tests. |

Git mirrors and Git LFS downloads are distinct backup steps; GitHub's backup documentation treats them separately. GitHub-specific records must likewise be represented as separate coverage, not silently included in a “Git backup” claim. [S1]

### 1.3 Explicit limits

The initial protected scope is Git objects and refs reachable through the source's authorized interfaces, required LFS objects, and enabled wiki repositories. Unpushed commits, dirty working trees, inaccessible refs, deleted data never captured, and unavailable LFS content are not automatically protected.

Issues, pull requests, discussions, release attachments, packages, and account settings are later connectors. Mark them **not included** until implemented and verified. Exporting platform metadata is not a promise that another host can import it with identical behavior.

A private bucket is not protection against every threat. Losing the bucket account or all recovery keys can still lose access. Protection against a compromised writer or storage administrator requires separately enforced retention and/or an independently administered second copy.

**Not building:** a GitHub replacement, pull-request UI, CI runner, arbitrary workflow engine, cloud billing platform, multi-tenant SaaS control plane, or active-active storage system.

## 2. User experience

### 2.1 One obvious path, advanced controls one step away

Progressive disclosure separates frequent decisions from specialized controls; it must also make the path to those controls clear. [S2] Apply it to the product with just two help levels:

| Normal path | Reveal when relevant |
|---|---|
| Protect, status, restore, doctor | Sources, storage, jobs, hosting, retention, credentials |
| “Where should protection run?” | Service user, cache location, resource limits |
| “Where should recovery points live?” | Endpoint, region, request concurrency, provider capabilities |
| “What needs attention?” | Per-repository evidence, logs, checksums, request IDs |
| “Recover to a new folder” | Historical refs, remote destination, approved overwrite plan |

`gitpickle help` shows the normal path and a single pointer to `gitpickle help --all`. Do not require users to learn the internal domain model. Do not turn setup into a sequence of `init`, `configure`, `register`, `enable`, and `start` commands.

KISS also means **one canonical verb per job**. `protect` creates an ongoing policy; `backup` runs a capture now; `sync` refreshes the source-tracking state; `restore` recovers retained data. There is no implicit two-way synchronization.

### 2.2 First setup

The entry point is `gitpickle protect github`. Ask only for information that cannot safely be inferred:

1. **Execution:** this machine, a Linux server reached over SSH, or a Coolify deployment (guided OCI-image setup).
2. **Storage:** R2, S3, or B2, with provider-specific connection guidance.
3. **Source scope:** authenticate GitHub and confirm the repositories the credential actually exposes.

Remember completed steps. Resuming setup must not recreate buckets, duplicate jobs, or repeat authorization unnecessarily. Browser actions stay in the browser; secrets use a masked prompt or credential provider, never a suggested command-line argument.

```text
$ gitpickle protect github

Run protection on: backup@my-server
Store recovery points in: R2 / private-git
Source: GitHub / alex

404 authorized repositories found.
Protect these and future repositories this authorization can access? [Y/n]

Plan
  Install a user-scoped service on my-server.
  Back up every 6 hours; discover newly authorized repositories daily.
  Capture Git history, required LFS objects, and available wikis.
  Keep all recovery points until you choose a retention policy.
  Do not change your GitHub repositories or local Git remotes.

Apply this plan? [Y/n]

Protection is running on my-server.
1 repository verified · 403 pending

Next: gitpickle status
```

This transcript omits credential-entry screens, not permission checks. Installation must show any privilege escalation, verify the server identity, and disclose runtime dependencies. Existing buckets are supported; automatic bucket creation requires a separate explicit permission, not account-wide credentials by default.

A GitHub credential may not reveal repositories it cannot access. Say **“authorized repositories found,”** not “all repositories in your account,” unless coverage has actually been established. A disappearing repository is “source unavailable,” not automatically “deleted.”

For Coolify, the initial flow can generate a version-pinned container recipe and guide the user through adding secrets in Coolify; remote API-based provisioning is optional and must be separately authorized. For a Linux VM, offer an SSH bootstrap that installs the binary and a systemd service after a visible plan. Detect an existing Tailscale installation and offer private HTTPS hosting without requiring an unrelated Tailscale account permission.

Before applying, validate authentication, available staging disk, bucket access, and the capabilities required by the chosen mode. Storage probes use an isolated test prefix; no destructive tests against existing archive data. Show a size estimate only when it is based on evidence, and label unknown LFS size and transfer charges as unknown.

### 2.3 Defaults that minimize decisions

| Setting | Initial default | Reason |
|---|---|---|
| Source changes | Capture every 6 hours | A visible, understandable starting policy; configurable. |
| Freshness warning | More than 8 hours without successful source confirmation | Scheduling is not a guarantee; failures need a separate signal. |
| New repositories | Discover daily within confirmed authorization | Avoid repeated manual registration. |
| Deletion | Never delete retained data automatically in the first release | Safety before retention automation. |
| Git hosting | Off until enabled; source mirrors read-only | Avoid accidental public exposure or conflicting writers. |
| Restore destination | A new local directory | Avoid overwriting the user's work. |
| Telemetry | No vendor telemetry by default | Private operation should not require reporting home. |

These are proposed defaults, not measured service-level guarantees. Keeping everything increases storage consumption; show growth and warnings before it becomes a surprise. Later retention presets require an impact preview and dependency-safe deletion.

### 2.4 Everyday status

`gitpickle` is a read-only status command. Inside a recognized checkout it defaults to that repository; elsewhere it shows the selected server. Always print the effective scope. Write operations require an explicit repository, an unambiguous current repository, or `--all`; a changed working directory must never silently turn a single-repository action into an account-wide action.

```text
$ gitpickle

Server: my-server   Scope: all 404 registered repositories

402 current and verified · 2 need attention
Source checked: 12 minutes ago
Independent restore test: 1 repository passed yesterday

Needs attention
  alex/media     Required LFS objects are missing.
  alex/archive   GitHub access was lost; last recovery point is 3 days old.

GitHub issues, pull requests, and release assets: not included.
Next: gitpickle doctor --attention
```

The headline comes from a coverage calculation, not the last successful job exit code. One sampled restore must never become “404 repositories restore-tested.” An unchanged repository can be current after a fresh source check even though its retained snapshot is older.

For each problem, answer three questions: **What still exists? What is currently at risk? What should I do next?** Present one primary next action; keep stack traces behind `--debug`.

### 2.5 Protection without changing Git habits

Inside a checkout, `gitpickle protect` registers the remote repository for scheduled protection after showing the detected source. It does not upload uncommitted files, capture unpushed branches, install hooks, or replace `origin`.

```sh
# Hosting examples, once an authenticated HTTPS endpoint is enabled.
git clone https://git.example.com/alex/project.git
git remote add pickle https://git.example.com/alex/project.git
git fetch pickle
```

Standard push support follows in the writable-hosting milestone: `git push pickle main` works after that repository is explicitly placed in hosted mode. Git's HTTP backend provides established upload-pack and receive-pack integration; compatibility must be tested rather than inferred from a working clone. [S3]

Keep two explicit repository modes:

- **Protected mirror:** GitHub controls the current source view; retained recovery points preserve previous views. Direct client pushes are rejected with an explanation.
- **Hosted repository:** clients can push; source mirroring cannot overwrite these refs. Converting a mirror creates a checkpoint and pauses source-driven updates. Reconciliation is an explicit operation.

This prevents the most confusing failure: a background sync overwriting commits someone just pushed to their backup server.

### 2.6 Restore when stressed

`gitpickle restore` guides the user through repository, recovery point, and destination. Default to the newest complete, verified point and a new directory; incomplete points remain inspectable but need explicit selection and a missing-data warning.

```text
$ gitpickle restore alex/project

Recover: alex/project
Point: 2026-10-14 12:00 UTC · Git and required LFS verified
Destination: ./recovered/alex/project
Existing files overwritten: none

Restore? [Y/n]
```

The ordinary command also handles a missing server:

```sh
gitpickle restore --from-storage
```

That opens a storage-and-key recovery path instead of requiring a second product vocabulary such as “rescue,” “rehydrate,” or “bootstrap.” It reconstructs the inventory, offers a dry run, downloads the selected data, and reports an integrity result. It does not require DNS, GitHub, the old machine, or a control-plane account.

At setup, create a nonsecret recovery card containing the provider, bucket/prefix, archive ID, format version, software retrieval instructions, and key-location guidance. Keep decryption material and offline software copies separately protected. Never imply that a key stored only on the lost daemon can be recovered from the bucket.

### 2.7 CLI accessibility and predictable automation

Provide keyboard-only prompts, non-ANSI output, `NO_COLOR` support, readable narrow-terminal layouts, and status words rather than color-only signals. Never prompt when stdin is not a terminal; report missing arguments instead.

`--json` emits a versioned result to stdout; progress and diagnostics go to stderr. No terminal prose is mixed into MCP stdio or machine output. Use stable error codes and explicit time zones. Interactive relative dates must resolve to an exact timestamp before approval.

A long-running operation returns a job ID only after durable acceptance. `--wait` follows the job; Ctrl-C detaches the client by default, explaining that server work continues. Cancellation is a separate operation that cannot undo an already committed recovery point.

### 2.8 Optional full-screen TUI, not a required workflow

**Decision: yes, a TUI ships, but it is opt-in.** `gitpickle protect github` remains a simple guided terminal flow; `gitpickle` remains a fast, readable status report; `gitpickle tui` opens the keyboard-driven application when someone needs to explore 404 repositories, browse jobs, and choose recovery points. Avoid automatically switching to a full-screen app just because stdout is a TTY. The TUI uses the same application API and authorization semantics as the CLI and MCP—no separate business logic.

A proposed first-screen shape:

```text
$ gitpickle tui
┌─ GitPickle / my-vps ────────────────────────────────────────────┐
│ 404 repositories · 402 verified · 2 need attention              │
│ GitHub checked 12 min ago    R2 archive healthy                 │
├────────────────────────────┬───────────────────────────────────┤
│ / Search repositories      │ alex/media                        │
│   alex/app                │ Last good snapshot: 3h ago        │
│ ! alex/media              │ Warning: 2 LFS objects missing    │
│   alex/notes              │ [V] View recovery points          │
│   …                       │ [B] Retry backup                   │
├────────────────────────────┴───────────────────────────────────┤
│ / search   Enter details   r refresh   ? help   q quit        │
└────────────────────────────────────────────────────────────────┘
```

Progressive disclosure applies inside the TUI: overview → search/filter → repository details → snapshots/job history → explicit operations. Dangerous actions open a review screen with the selected scope, irreversible consequences, and authority required. The TUI must display backup coverage and hosting mode separately. Provide screen-reader-friendly plain CLI alternatives for everything; support small terminals, keyboard navigation, and `NO_COLOR`. No TUI code runs for `--json`, MCP stdio, or noninteractive automation.

### 2.9 Native Git: familiar commands, honest modes

**Decision: the server speaks standard Git Smart HTTP.** A user running ordinary Git does **not** install the GitPickle CLI. A tailnet-only Git URL works the same way as a public HTTPS Git URL, assuming the client can reach the endpoint and authenticate. SSH Git remotes (`git@host:owner/repo.git`) are a future transport; SSH used to install/administer the daemon is not the Git SSH protocol.

```sh
# Clone a GitHub-protected, read-only mirror from GitPickle:
git clone https://git.example.com/alex/project.git
cd project
git fetch origin
git pull --ff-only

# Keep GitHub as your main origin, add the independent protected remote:
git clone https://github.com/alex/project.git
cd project
git remote add pickle https://git.example.com/alex/project.git
git fetch pickle
git log pickle/main --oneline -5
# git push pickle main → rejected while GitPickle is in protected-mirror mode.

# Explicitly hosted mode (after writable hosting passes its release gate):
git clone https://git.example.com/alex/hosted-project.git
cd hosted-project
# edit, stage and commit normally
git push origin main
git push origin --tags
git lfs push origin --all
```

Use a credential manager or supported Git credential helper for authentication—never prescribe tokens embedded in URLs or shell history. A newly hosted repository may require creation through `gitpickle repo create` (or an explicitly enabled create-on-push policy), but the Git protocol remains entirely standard. A `git pull` only changes the local checkout according to Git's configured integration policy; GitPickle does not invent a new meaning for Git verbs.

**Modes and compatibility:**

| Mode | `git clone` | `git fetch` / `git pull` | `git push` | Upstream policy |
|---|---|---|---|---|
| Protected mirror (initial) | Yes | Yes | **Rejected** (read-only) | GitHub remains source; old recovery points remain retained |
| Hosted repository (writable milestone) | Yes | Yes | Yes, authenticated | Background GitHub mirroring cannot overwrite hosted refs |

The compatibility suite must test `git clone`, `fetch`, `pull`, normal and force-with-lease pushes, branches/tags, shallow/filtered clones where advertised, and Git LFS. Do not equate GitHub features (PRs, Actions) with Git protocol support. CLI/TUI display the copyable canonical remote URL and mode, rather than making the user guess.

## 3. Agent experience (AX)

### 3.1 A small operator interface, not a shell

`gitpickle mcp` runs a local stdio MCP server using the selected client profile and a constrained credential. It calls the same application use cases as the CLI. An authenticated remote MCP transport is optional later; local stdio is the smallest initial surface. MCP defines stdio and Streamable HTTP transports. [S4]

Default tools are few and task-oriented:

| Tool | Purpose | Default authority |
|---|---|---|
| `protection_status` | Summarize coverage, freshness, and evidence | Read |
| `repositories_list` | Find repositories, with pagination and filters | Read |
| `recovery_points_list` | Inspect available points and completeness | Read |
| `jobs_get` | Check progress, outcome, or sanitized diagnostics | Read |
| `backup_run` | Start or reuse an idempotent backup job | Operator grant |
| `restore_plan` | Preview recovery, destination, and consequences | Read/plan |
| `plan_apply` | Execute a specific authorized plan | Scoped approval |

Do not expose generic shell execution, arbitrary filesystem access, unrestricted URL fetching, raw credentials, or retention deletion as convenient agent tools. Log access is separate from source-code access, and logs are redacted before being returned.

### 3.2 Plans are real security objects

An agent may prepare a restore or configuration change. A trusted CLI session or preauthorized policy must approve it. The server binds approval to the actor, repository IDs, destination, destination visibility, expected state versions, plan digest, expiry, and impact limits. Changing any of these invalidates approval.

A boolean such as `approved: true` supplied by the model is not approval. Tool descriptions and MCP annotations are not access controls. Apply authorization in the application service, independent of transport; validate again before execution. MCP security guidance is a reference for the transport boundary, not a substitute for these domain policies. [S5]

Repository text, branch names, commit messages, API responses, and logs are untrusted data, including when they contain instructions to an agent. Keep them out of authorization decisions. MCP restores use approved destinations or a managed sandbox, not arbitrary paths on the daemon.

### 3.3 Predictable answers and limited agent effort

Results contain an observation timestamp, scope, stable IDs, coverage gaps, evidence references, and a structured next action. Distinguish `queued`, `running`, `committed`, `verified`, and `failed`; do not turn accepted work into an immediate success claim.

Support idempotency keys, bounded pagination, resumable job inspection, and explicit retry advice. Default status is a compact summary; agents request per-repository evidence only when needed. A question such as “What is not recoverable?” should require one status call and, at most, one detail call—not hundreds of tool invocations.

A generated integration configuration must contain a profile reference, not a pasted storage secret. Installation helpers preview changes and preserve existing client configuration. The application works without an LLM.

## 4. Architecture decisions

### 4.1 One deployable application, modular internally

Use a **Rust modular monolith** as the proposed implementation baseline. Choose it for a small distributable management application and strong module boundaries, not because of an unmeasured speed claim. A time-boxed implementation spike can revise the language decision if team expertise makes another choice materially simpler.

```text
Client machine                         Server
CLI ───── authenticated management ──► Application services
MCP stdio ─── same client API ────────►   ├─ source discovery and protection
Ordinary Git ───── Git HTTPS ────────►   ├─ Git access and recovery
                                        └─ durable jobs and authorization
                                                │
                                  Git adapter / archive adapter
                                      │                 │
                                 native Git       R2 / S3 / B2
                                      │                 │
                              bounded local cache   durable archive
```

One executable does **not** mean inventing a Git implementation. **Prefer the MIT-licensed walgit Rust crates for Smart HTTP, LFS and object-store WAL**, linked into the single GitPickle process where feasible. A focused fork is preferable to an unmanaged long-lived child process if tighter integration is required; keep a narrow `GitEngine` interface so that walgit internals cannot infect domain models. The daemon may still depend on a supported native `git` binary for pack/maintenance operations, which must be stated in packaging; Git clients need only ordinary Git. Distribution, updates, and licensing must disclose the distinction. [S16]

Run one daemon and one embedded SQLite database. Do not introduce Redis, Kafka, Kubernetes, independent microservices, a plugin marketplace, or an S3 filesystem mount. Workers inside the daemon execute a small known set of jobs, not arbitrary user scripts.

### 4.2 Domain-driven boundaries

Use domain-driven design to separate responsibilities and language, not to add layers around every function. Evans' reference provides the pattern vocabulary; the proposed boundaries below are specific to this product. [S6]

| Bounded context | Owns | Does not own |
|---|---|---|
| **Protection** | Source identity, repository registration, capture policy, coverage and freshness | Git HTTP or bucket SDK types |
| **Archive** | Immutable data, manifests, commit evidence, verification, future retention | GitHub discovery or user prompts |
| **Git Access** | Hosted refs, repository mode, Git sessions, push acceptance rules | Deleting backup history |
| **Recovery** | Recovery selection, plans, dependency resolution, restore validation | Source availability |
| **Execution & Access** | Durable jobs, scheduling, actors, grants, approvals, audit records | A second implementation of domain policies |

Execution and access can be separate modules within a supporting context; split further only when ownership or invariants justify it. Each context owns its writes and schema. Cross-context work goes through typed application interfaces. No shared mutable “repository model” imported everywhere.

Use internal IDs and GitHub's source ID mapping so rename or transfer is not confused with deletion and recreation. A repository name is an alias; it is not sufficient proof that two repositories are the same data source.

### 4.3 Storage and durability

The durable archive contains immutable Git packs, LFS content, complete recovery manifests, and the minimal records needed to reconstruct committed state and accepted operations. Local SQLite is an operational index, not the only copy of a recovery catalog or accepted job.

Use a narrow storage interface and explicit provider capability tests. S3 supports conditional-write operations, while S3-compatible services expose their own behavior and restrictions; a matching endpoint shape is not a portability proof. [S7], [S8]

The first deployment model is **one active daemon writer per archive**, with serialized mutation per repository. No distributed leases or automatic failover. A second server can inspect or restore read-only; writable takeover requires stopping and fencing the old writer. A local process lock is not cross-server fencing.

Immutable application records do not make a bucket administratively immutable. Offer verified provider retention controls separately; R2 bucket locks are one provider-specific mechanism. Never claim retention is enabled merely because the application does not normally delete objects. [S9]

### 4.4 Keep backups and hosting separate at the transaction boundary

Backups stage and validate new content before publishing a committed recovery point. Git hosting can read only committed generations. Writable hosting must also make an accepted push reconstructible from object storage before reporting success to the client.

A naive `post-receive` upload is insufficient: Git documents that this hook runs after ref updates and cannot change receive-pack's outcome. [S10] The writable milestone therefore requires a tested transaction coordinator, not a shell hook that asynchronously copies a bare repository.

Detailed data layouts, failure semantics, and the proposed push integration are in Appendix A.

### 4.5 walgit adoption: build on it, but preserve independent backup guarantees

**Recommendation:** start a bounded adoption spike against [walgit](https://github.com/tobi/walgit), whose documented features include Git Smart HTTP v0/v2, receive-pack, Git LFS, S3/GCS object storage, an object-store WAL, conditional publication, and upstream-follow functionality. It is an attractive engine, **not yet a validated implementation dependency** for this proposal. [S16]

**Local fork:** the project keeps its walgit fork at `~/walgit`—a clean checkout of `tobi/walgit`, `main` branch. Run the adoption spike there. If the fork diverges, keep the diff narrow enough to review against upstream.

| Own / compose in GitPickle | Reuse / adapt from walgit if proven |
|---|---|
| GitHub inventory and scoped authorization | Standard Git HTTP transport and Git data access |
| Protection policy, durable job state, source checks | Native Git receive/fetch compatibility |
| Historical snapshots and retention manifests | Object-store WAL, commit publication, caching |
| Bucket-only reconstruction and restore testing | Git LFS serving and hosted repository refs |
| CLI/TUI/MCP and user-readable coverage | Server-side Git auth primitives, as suitable |

**Critical distinction:** walgit's history/WAL and up-to-date mirrored refs do not by themselves prove that GitPickle has immutable, independently discoverable recovery points across force-pushes or source deletion. GitPickle's Archive context must publish its own durable, versioned recovery catalog; tests must recover from the bucket on a clean machine even if the walgit cache, process database, and original server are lost.

Before choosing a vendor/fork/linking strategy, run a spike covering: crate API and build integration; license/dependency inventory; Git SHA-1/SHA-256 and LFS interop; private Git HTTP credentials; a mixed 404-repo fixture; source history rewrite; crash before/after acknowledgment; snapshot portability; R2/S3/B2 conditional-write semantics; and cost/performance against stock Git HTTP. **Do not assume S3-compatible means correct conditional publication on each provider.** Keep one active GitPickle archive writer in the first implementation even if walgit supports concurrent writers under a stronger provider contract.

Decision gate: adopt walgit as the Git engine if these tests pass and we can maintain a single manageable executable. Otherwise retain the adapter boundary and use standard Git HTTP + an explicitly tested durability coordinator. Never postpone the backup MVP solely to acquire writable Git hosting.

### 4.6 Packaging and private-network deployment

**Supported deployment shapes:**

| Target | Packaging | Network | Durable state |
|---|---|---|---|
| Linux VM / bare metal / home server | Release binary + systemd | Tailscale-only by default; optional public HTTPS | R2/S3/B2 archive; local cache/index persisted if convenient |
| Coolify | Official version-pinned OCI image or Compose service | Coolify reverse proxy for public HTTPS **or** host Tailscale Serve for private HTTPS | Same bucket; Docker volume for indexes/cache and runtime configuration |
| Docker / Podman without Coolify | Same OCI image | Reverse proxy, loopback, or tailnet | Same bucket; state volume optional for speed, not recovery |
| Laptop / workstation | CLI binary, optional local daemon | SSH/Tailscale to the chosen daemon | Local client credentials only |

An official container should expose health/readiness endpoints, log to stdout/stderr, run unprivileged, respond to SIGTERM with graceful job handling, and offer an immutable image digest. The canonical entrypoint is `gitpickle serve`. Secrets come from a credential store or runtime environment, not committed Compose YAML. Losing the container or cache volume must not invalidate committed backups; accepted jobs and recovery evidence must be reconstructible from durable data. The product does **not** require Docker, Coolify, Redis, or a local S3 emulator in production. Coolify supports standalone images and Compose-based services. [S17]

**Private Tailscale example (VM):** run GitPickle over HTTP bound to loopback (`127.0.0.1:8080`) and terminate HTTPS through Tailscale Serve on the host:

```sh
# Conceptual GitPickle command; CLI syntax to be implemented:
gitpickle serve --listen 127.0.0.1:8080

# Real Tailscale command, after MagicDNS / Serve HTTPS are enabled:
tailscale serve --bg 8080

# From a client inside the same tailnet (example generated tailnet hostname):
git clone https://gitbox.example-tailnet.ts.net/alex/project.git
```

Tailscale Serve automatically provides a private HTTPS endpoint for the tailnet; clients must be members with permitted access. The tailnet route is *network* access control, not a substitute for per-repository authentication and authorization. Do not silently enable public Funnel. For Coolify + host Tailscale, make the container reachable **only from the host loopback** (for example by binding its Docker port to `127.0.0.1`) and point host Tailscale Serve at that port; don't assign a public Coolify domain to a private-only instance. [S18], [S19]

For a public domain, supply a documented Coolify proxy/reverse-proxy recipe with HTTPS, streamed request bodies, Git/LFS size and timeout settings, and protected management routes. An all-private deployment may still reach GitHub and R2/S3/B2 via outbound network access. First-run setup should offer **“Private to my Tailscale”** or **“Public HTTPS domain”** only when Git hosting is enabled; backups alone do not require an inbound endpoint.

The one-command experience is a *wrapper around prerequisite discovery and confirmation*, not a claim that GitPickle can enroll devices into an unrelated Tailscale account or provision arbitrary DNS without permission. Health checks, recovery drills, and benchmarks must run for both a binary+systemd deployment and a Coolify container deployment.

## 5. TLA+ and correctness

Use TLA+ where interleavings, crashes, and retries create risk. Do not formalize prompt rendering or every CRUD endpoint. TLC can explore finite-state models of these transitions; results apply to the model and its assumptions, not automatically to the Rust implementation or cloud service. [S11]

The first specifications are:

| Model | Main question |
|---|---|
| `ArchiveCommit.tla` | Can a published recovery point reference missing data after any modeled crash? |
| `JobReplay.tla` | Can retry, restart, or duplicate delivery create conflicting committed outcomes? |
| `GitPublish.tla` | Can a successful push disappear when the daemon disk is lost? |
| `Retention.tla` | Can cleanup delete something still needed by a retained point or active restore? |
| `PlanApproval.tla` | Can stale or changed plans execute under an old approval? |

Write the relevant model before implementing the corresponding durable state transition. Archive commit and job replay come first; retention remains disabled until its model and tests exist. Map each model action to an implementation boundary and a fault-injection point.

**Release claim:** “These bounded models and assumptions passed TLC, and their failure traces have implementation tests.” Do not claim that model checking proves the whole application correct. Appendix B specifies invariants, assumptions, exploration bounds, and traceability.

## 6. Benchmark-driven development

### 6.1 Every important behavior gets a cost model

Before implementing a hot path, record its user outcome, correctness contract, benchmark fixture, primary metric, and performance budget. After implementation, compare it to a pinned baseline. Review correctness and performance together; never trade away durability merely to improve latency.

Measure the costs users experience: time to first verified recovery point, backup freshness, restore time, push durability latency, resource use, object-storage requests, transferred bytes, and agent tool round trips. CPU microbenchmarks alone are insufficient.

### 6.2 A small performance dashboard from the first vertical slice

The benchmark runner writes JSON results and generates a static report. No monitoring service is required. Each row links a commit, scenario version, machine fingerprint, raw samples, confidence interval, correctness result, and baseline comparison.

Use Criterion for Rust microbenchmarks and Hyperfine for process-level CLI timing, adapting results into a project-owned, versioned JSON schema. Use a dedicated harness for network, Git, storage, resource, and recovery scenarios. [S12], [S13]

Initial candidate budgets, to validate rather than advertise:

| Scenario | Candidate budget or rule |
|---|---|
| Local `--help` startup | p95 ≤ 100 ms on the pinned reference runner |
| TUI first useful paint and navigation | Record cold/warm startup, p95 search/filter over 404 repos, peak terminal refresh/CPU; establish budgets from reference hardware |
| Tailscale Git fetch and Coolify cold container | Track end-to-end Git HTTP latency, startup, restart-to-ready, and remote clone success by deployment profile |
| Warm status for 404 registered repositories | API service time p95 ≤ 100 ms; loopback CLI p95 ≤ 200 ms |
| Idle daemon with 404 registered repositories | ≤ 128 MiB RSS, excluding active Git children; also report whole-process-tree usage |
| Unchanged incremental capture | Zero Git/LFS payload bytes uploaded; small metadata writes reported separately |
| Warm Git clone of the 100 MiB fixture | Median completion time ≤ 1.25× a native Git HTTP control under the same conditions |
| Durable push | 100% of acknowledged updates recoverable after injected server loss; latency tracked separately |
| Full bucket-only restore | Every selected ref and required LFS object verified; publish elapsed time, bytes, requests, and peak disk |

These are starting engineering hypotheses. Numeric latency gates activate only after runner noise has been characterized. No fixed restore-time promise can be inferred from a repository count alone.

### 6.3 Pull-request, nightly, and release gates

Every pull request runs correctness tests, relevant bounded models, and a short deterministic benchmark suite. The Git-engine decision spike establishes **walgit vs stock Git HTTP** performance and correctness baselines before accepting the dependency; repeat representative measurements when upgrading or rebasing the engine. A pinned performance runner compares material changes to both the merge base and a fixed release baseline. Re-run apparent regressions before blocking on noisy timing.

Nightly jobs cover the 404-repository portfolio, cold-cache scenarios, large Git/LFS workloads, crash injection, and real-provider canaries. Real-provider tests are separately credentialed and labeled; emulator results never stand in for provider compatibility.

A release requires a report covering warm and cold behavior, scalability, request amplification, memory/disk ceilings, restore correctness, and a clean-machine recovery drill. Record accepted regressions with an owner, rationale, expiry, and revised budget—not by silently replacing the baseline.

Appendix C defines fixtures, measurement protocol, regression thresholds, report schema, and developer commands.

## 7. Delivery plan

Deliver vertical slices with evidence gates, not a long list of partially working features.

| Milestone | User outcome | Required evidence |
|---|---|---|
| **M0: contracts and prototypes** | A user understands protection, coverage, and recovery without documentation | Tested CLI/TUI transcripts; walgit adoption spike; domain vocabulary; first TLA+ model; deterministic fixtures and baseline harness |
| **M1: one-repository recovery** | Back up one private Git repository and restore it using only storage access | Immutable manifest; lost-disk recovery; corruption detection; capture/restore benchmark |
| **M2: reliable portfolio protection** | Protect the authorized 404-repository portfolio through the guided CLI | Discovery pagination; LFS/wiki coverage; durable jobs; retry fairness; all three provider adapters passing contract tests |
| **M3: operator release** | Operate through CLI/TUI/MCP and `git clone` / `git fetch` from read-only Git hosting on VM, Coolify, or a tailnet | Authentication/authorization tests; container/systemd recipes; private Tailscale deployment test; truthful status; recovery card; compatibility matrix; published baseline report; brand clearance or a fallback-name decision (section 9) |
| **M4: writable Git hosting** | Use standard `git push` / Git LFS against the service within the published compatibility scope | Git publication model; crash matrix; durable acknowledgment tests; standard Git client acceptance suite; contention and latency report |
| **Later, separately gated** | Retention cleanup, second-copy replication, richer metadata, remote MCP | A model and restore/benchmark evidence for each new safety boundary |

The first public backup release may end at M3. It must say “read-only hosting,” not “full Git hosting.” The main product goal includes M4.

Cloudflare Workers deployment is not an initial target: this design assumes native Git processes, persistent job execution, and staging disk. A future edge gateway may delegate to the daemon. A Workers-native Git engine would be a separate compatibility and resource-limit project, not a packaging switch.

## 8. Definition of done

A feature is done when its user-visible outcome, failure behavior, domain ownership, authorization rules, relevant model, tests, benchmark, and documentation agree.

Release acceptance includes the following scenarios:

| Scenario | Expected result |
|---|---|
| Source account becomes unavailable | Old points remain restorable; freshness turns stale; no automatic deletion |
| Upstream force-push or branch deletion | Prior captured history remains available through retained points |
| LFS download is incomplete | Git-only progress may be retained; overall required coverage cannot show verified |
| Daemon dies during upload | Incomplete objects are not published as a complete point |
| Daemon disk disappears after a successful operation | Committed data and accepted job records remain discoverable in storage |
| Two jobs target one repository | Mutations serialize; duplicate delivery does not fork committed history |
| A direct push conflicts with mirroring policy | The product rejects or requires an explicit mode change; no silent overwrite |
| An agent changes a previously approved destination | Execution is denied until the new plan is authorized |
| Clean-machine recovery with GitHub blocked | Selected data is restored from storage and independently checked |
| A hot path regresses | A reproducible comparison appears in review; exceptions are explicit |

Usability acceptance is also required: in a moderated study with at least five representative users, at least four should complete setup without editing a configuration file, identify an incomplete backup correctly, and restore to a new directory without assistance. Every participant must be able to distinguish “configured,” “verified,” and “fresh.” This is a proposed validation gate, not a research result.

**Product test:** can a user make a correct decision from the first screen, and can an engineer demonstrate why that screen is telling the truth?

---

## 9. Brand and naming: GitPickle

### 9.1 Naming decision and its blocker

**Decision: the working name is GitPickle. It replaces GitVault.** The name is memorable, easy to pronounce, and fits a mascot. It joins two ideas: a pickle is food preserved for later, and “in a pickle” means trouble. The preservation metaphor maps onto the architecture without asking users to understand packs, manifests, LFS, or object-storage transactions.

**Blocker: the Git trademark policy.** The policy states that third-party products generally may not incorporate Git as a syllable in a new coined name. It names name shapes like GitPickle as restricted. Formats such as “Product Name for Git” are allowed under its conditions. [S20]

The policy does not make the name impossible. It means the project needs written permission from Software Freedom Conservancy before public use. Send the proposed name, the CLI command, a product description, and the open-source licensing intent to trademark@sfconservancy.org. Request a written determination. GitHub and GitLab hold separate licensing arrangements; their existence is not a general exception.

**Status: creatively strong, not cleared for public use.** Build under the GitPickle name. Hold all public branding until clearance arrives.

### 9.2 Availability findings

Research on 14 October 2026 found:

| Surface | Finding | Assessment |
|---|---|---|
| Exact product name | No established Git backup product named GitPickle found in indexed searches | Encouraging, not cleared |
| GitHub repository name | No exact public project found | Encouraging |
| GitHub account `@gitpickle` | Occupied by a personal account with no public repositories | Confirmed collision |
| Similar name GitPick | An existing, actively published repository-copying CLI | Search and recognition overlap |
| gitpickle.com, .dev, .io, .app | Live registration status not verified | Unknown |
| npm, crates.io, PyPI | Package ownership not verified | Unknown |
| Git trademark permission | Policy raises a material issue | Written clearance recommended |

Search-engine silence does not establish availability. GitPick is a different kind of tool, but the names are close enough to create noise in search results, documentation, and recommendations.

One secondary association: programmers may read “pickle” as Python's serialization format. Treat that as an amusing extra layer, nothing more. The archive format must never use or imply Python pickle, because deserializing untrusted pickle data is a documented security hazard.

### 9.3 Identity direction

The identity must look playful at first glance and stay trustworthy when someone recovers hundreds of repositories.

| Direction | Look | Palette | Line |
|---|---|---|---|
| **A. The Pickle Jar** | Vintage deli label meets modern tooling: a simplified preserving jar holds a branching Git graph; heavy type; slightly imperfect outlines | Dill, lime, cream, mustard | “Your commits deserve a long shelf life.” |
| **B. The Gherkin Guardian** | An open-source mascot: a small pickle that looks after everyone's commits; reaction stickers for releases | Leaf, lime, ivory, coral | “In a pickle? Git it back.” |
| **C. Dead Serious Dill** | A tiny pickle icon beside a confidently minimal wordmark; dark and restrained, humor kept quiet | Ink, neon dill, mist, sage | “Preserved for when things go sour.” |

**Decision: A + B together.** The jar is the primary logo. The mascot serves the README, onboarding, stickers, and release announcements. This gives the project a recognizable identity without making the recovery experience childish.

These directions are explorations, not cleared logo assets.

### 9.4 Voice: deadpan competence

The brand personality is careful engineering with occasional terrible pickle jokes. The contrast is the point. A tool named GitPickle can still be uncompromising about immutable data, recoverability, and correctness.

Conceptual homepage hero:

```text
🥒 GitPickle

Keep your history. Lose the panic.

Automatically protect your Git repositories, preserve verified
recovery points in your own storage, and restore without depending
on GitHub or your original server.

Open-source · Self-hosted · Independently recoverable
```

Campaign line: **In a pickle? Git it back.**

Phrases to keep in the vocabulary:

- “Your commits deserve a long shelf life.” — launch material.
- “That's kind of a big dill.” — release milestones.
- “Things went sour. Your history didn't.” — disaster-recovery campaigns.
- “The code survives. The panic doesn't have to.” — serious documentation.

**The application itself stays boringly precise.** Commands and status messages use plain operational words: `gitpickle protect github`, `gitpickle restore --from-storage`, `gitpickle doctor`. Do not rename `restore` to `unpickle`, call a backup job “brining”, or label failed backups “sour pickles”. Progressive disclosure and evidence-based status are worth more than a joke stretched into the interface.

### 9.5 The brand promises only what the product contracts

| Brand idea | Engineering contract |
|---|---|
| Preserve | Immutable Git recovery points and historical protection |
| Keep it somewhere safe | User-controlled R2, S3, or B2 storage |
| Check the seal | Integrity verification and independent restore tests |
| Open the jar anywhere | Recovery without the original server or GitHub |
| Keep using Git | Standard Git HTTPS access; read-only hosting first |

Branding must reinforce these contracts, not overpromise. This proposal is a design, not a shipped capability.

### 9.6 Clearance checklist before public use

| Check | Where | Notes |
|---|---|---|
| Domains | gitpickle.com, .dev, .io, .app | Check each registration individually |
| United States trademarks | USPTO | Exact and confusingly similar names |
| European Union trademarks | EUIPO | Include software and hosted-service categories |
| International trademarks | WIPO | Search GitPickle, Git Pickle, GitPick, and variants |
| Rust crate name | crates.io | The implementation baseline is Rust |
| Git trademark permission | Software Freedom Conservancy | **Highest priority.** Obtain a written determination first |

The USPTO recommends considering confusingly similar marks, not only identical spellings. WIPO advises checking national and regional registries separately.

### 9.7 Fallback: RepoPickle

If permission is not viable, **RepoPickle** keeps the joke and removes the Git mark from the name:

```sh
repopickle protect github
repopickle restore
```

Preliminary searches found no exact existing product. That is not proof of available domains, handles, packages, or trademarks.

Do not use plain PickleJar as the fallback. A PickleJar software business exists, and a separate picklejar Python package is maintained.

---

## Appendix A. Domain and storage design

<details>
<summary>Expand domain language, transaction rules, storage format, and failure handling</summary>

### A.1 Shared language and ownership

| Term | Definition |
|---|---|
| Source | An authenticated upstream integration with an explicit authorization scope |
| Registered repository | A stable internal identity mapped to an upstream identity and human-readable aliases |
| Protection policy | Which components to capture, when to capture them, and how to evaluate freshness |
| Capture | One attempt to observe upstream state and preserve its required content |
| Recovery point | An immutable manifest describing a captured ref set and its content dependencies |
| Coverage | Component-by-component evidence of what a recovery point contains |
| Verification | A recorded check with a method, scope, result, and time; not a permanent health assertion |
| Restore test | Reconstruction from the archive followed by independent Git/LFS validation |
| Hosted generation | The committed ref state exposed by a writable Git repository |
| Job | A durable request with attempts, progress, and a reconciled terminal outcome |
| Plan | A versioned description of intended changes, preconditions, and required authority |

Use value types such as `RepositoryId`, `RecoveryPointId`, `ArchiveObjectId`, `GitObjectId`, `JobId`, and `PlanDigest`. Keep Git object hashes distinct from archive digests and provider ETags. An S3 ETag is not universally an MD5 content checksum. [S15]

The primary aggregates are a protected repository and its policy, an archive recovery point, a hosted repository generation, a recovery plan, and a job. Do not create one aggregate containing an entire account and its hundreds of repositories.

Events such as `CaptureCommitted`, `VerificationRecorded`, `SourceAccessLost`, `HostedGenerationCommitted`, and `RestoreCompleted` describe domain facts. Emit them after durable commit. Use in-process handlers and a durable outbox where needed; a distributed message broker is unnecessary.

Suggested dependency direction:

```text
CLI / HTTP / MCP adapters
          ↓
Application use cases and authorization
          ↓
Domain rules and typed ports
          ↑
Git / GitHub / object storage / SQLite / clock / credential adapters
```

The domain layer contains no HTTP framework, SQL queries, provider SDK types, terminal rendering, or MCP protocol objects. The application layer coordinates cross-context operations. Integration tests enforce dependency boundaries as well as behavior.

### A.2 Archive format v1

Prefer immutable pack files plus manifests over uploading a ZIP of every repository on every run. A manifest identifies the complete dependency set needed for that recovery point; it does not depend on an undocumented chain of local snapshots.

```text
archive.json                          # Archive identity and format information
repos/<repo-id>/identity/<record-id>  # Versioned names and source mappings
repos/<repo-id>/packs/<digest>       # Immutable, checksummed Git packs
repos/<repo-id>/lfs/<oid>             # Verified required LFS content
repos/<repo-id>/points/<point-id>     # Refs, HEAD, dependencies, coverage
repos/<repo-id>/commits/<commit-id>   # Publication and predecessor record
repos/<repo-id>/checks/<check-id>     # Verification and restore-test evidence
control/<record-id>                  # Minimal durable policies/job/audit records
```

This is a conceptual layout; commit the format only after the first restore prototype. Every record includes a schema version. Manifests preserve the Git hash algorithm, ref names and object IDs, symbolic HEAD/default-branch information, pack digests and sizes, required LFS OIDs, source identity, and parent publication identity.

Pack reuse is initially per repository. Avoid cross-tenant deduplication and a global Git-object database. Reuse already committed packs, upload new packs, and explicitly retain their dependencies. Do not publish thin packs that require an unavailable base. Compaction creates a new complete generation; it must not mutate old manifests.

A complete point may share immutable objects with earlier points. Its dependency inventory must nevertheless be independently traversable. Empty repositories need a valid metadata-only point, not a failed attempt to create an empty bundle. Export to a normal repository or Git bundle is an escape hatch; Git bundles support full and prerequisite-based transport, so exports must state which form they produce. [S14]

Indexes, pack indexes that can be regenerated, cache contents, and detailed transient progress are disposable. Durable source policies exclude raw credentials; restoring source automation may require reconnecting credentials even though recovering code does not.

### A.3 Backup publication protocol

The initial protocol operates under the explicit single-writer assumption:

1. Serialize mutations for the target repository and record a stable operation ID.
2. Fetch into isolated staging and record the actual captured ref set. Do not claim an atomic point-in-time snapshot across all 404 repositories.
3. Resolve and validate reachable Git content and required LFS content. Preserve component-level incompleteness where the upstream cannot supply data.
4. Upload new immutable objects with explicit digests. Handle multipart completion and ambiguous network responses idempotently.
5. Persist the immutable manifest, then the publication record that references it and its predecessor. All required dependencies must already be durable for a complete point.
6. Update rebuildable local projections. Record independent readback verification as separate evidence.
7. Report committed/verified coverage honestly. Release the repository mutation lock.

A no-change capture records a new source observation but reuses the recovery point. It does not upload the same Git/LFS payload again. A repository with changing LFS availability can receive new coverage evidence without pretending its missing objects were available earlier.

An upload timeout means **unknown outcome**, not necessarily failure. Reconcile using object identity and digest before retrying. A published commit with a lost client response must be discoverable without creating conflicting duplicate history.

Unreferenced uploaded objects are safe leaks until conservative cleanup exists. Leakage is preferable to deleting uncertain dependencies. Initial retention is append-only at the application level; there is no background archive garbage collector.

### A.4 Single writer and takeover

A daemon uses an OS-level singleton lock and per-repository mutation queues. The archive records writer identity and ownership information, but these records alone are not a distributed lock.

A different server opens an existing archive read-only by default. Writable takeover requires isolating the previous writer, revoking or restricting its credentials as needed, and reconciling committed state. No automatic timeout-based takeover is offered. If exclusive ownership cannot be established, remain read-only.

This is a deployment constraint, not a claim that all concurrent writers can be safely handled. Detect conflicting publication chains and stop; never choose the “latest timestamp” as a conflict resolution algorithm. A future multi-writer mode requires provider-enforced conditional publication, fencing, and a separate concurrency model.

The storage adapter contract tests listing/pagination, read-after-write behavior, retry ambiguity, multipart upload, digest verification, and any conditional operations actually used. Provider lock/versioning capabilities are declared independently. Run destructive conformance tests only in disposable test buckets.

### A.5 Writable Git hosting: integration gate

The preferred prototype delegates Git negotiation and validation to native Git while isolating mutation in a transaction workspace:

1. Authenticate and authorize the push, then hold the repository mutation lock.
2. Start from a committed hosted generation. Stage objects and ref changes without exposing them to concurrent readers.
3. Obtain the actual successful ref updates, including ordinary partial success or requested atomic behavior.
4. Persist the resulting object closure, manifest, and publication record in object storage.
5. Expose the new committed generation and only then release a successful Git protocol result to the client.

Implementation must preserve Git protocol semantics, not buffer arbitrary output and assume that is sufficient. Progress can stream, but final success cannot escape before durable publication. A storage failure must not leave an apparently successful, locally-only push. A crash after publication but before response may leave a committed operation whose client observed failure; retries must reconcile that ambiguity.

The spike must test native Git's integration points, hook behavior, atomic pushes, mixed ref results, and failure reporting. If a correct integration cannot be demonstrated, retain read-only hosting while that boundary is resolved. Do not replace the durability contract with asynchronous best-effort upload.

LFS is a separate data path. A successful LFS upload must itself be durable; a push introducing required LFS pointers must not be labeled fully recoverable while their objects are absent. Define and test the exact policy for rejecting missing-LFS pushes versus reporting incomplete coverage.

### A.6 Compatibility means a published matrix

The writable milestone tests ordinary clone, fetch, push, branch/tag deletion, annotated tags, force-with-lease, multi-ref pushes, atomic pushes, and LFS upload/download. Also test empty repositories, large refs sets, unusual valid names, and authentication failures.

Document support separately for shallow clones, partial-clone filters, Git protocol versions, SHA-256 repositories, signed push certificates, LFS locking, and server-side hooks. Preserve signed commit/tag objects as data; do not claim to replace the user's signature trust configuration.

Submodule pointers are preserved as Git content, but dependent repositories require their own authorized protection. Never recursively fetch arbitrary submodule URLs by default. Generic server-side code execution and GitHub platform features are outside the compatibility promise. SSH Git transport can be added later without changing the archive model.

### A.7 Durable jobs and bounded resource use

Use persisted command IDs and append-only acceptance/terminal records, with SQLite projections for scheduling. A job is acknowledged as accepted only after its recovery-critical request is durable. Restart reconciles previously accepted work; it does not require every progress update to be archived.

Execution is at-least-once with idempotent effects, not magical exactly-once execution. Mutating jobs require deduplication keys scoped to actor and operation. Two requests with the same key but different arguments conflict explicitly.

Apply bounded global concurrency, per-source rate limits, per-repository serialization, exponential backoff with jitter, and fair queues so one large repository cannot starve hundreds of small ones. Stop launching work when staging disk is low; preserve the last committed data. Cancellation interrupts only at safe boundaries and does not remove committed records.

Credential failures, rate limits, storage failures, integrity failures, and source absence use separate typed errors. Redacted audit events retain actor, scope, operation, policy revision, and result. Sensitive source URLs and credentials must not leak into process arguments, logs, or MCP output.

### A.8 Security and trust boundaries

The daemon is unprivileged, uses a private data directory, rejects unsafe paths and archive traversal, sanitizes Git configuration, and does not run repository-provided hooks. Apply resource/time limits to Git processes and defend source fetching against unexpected URL schemes and internal-network access.

The management endpoint defaults to local binding or an SSH-protected connection. Public HTTPS hosting is explicitly enabled with TLS and authentication; it never inherits public visibility from a source automatically. Backup-source credentials are read-scoped where possible. Destructive retention credentials are separate from routine writer credentials.

Provider-side encryption and TLS are a baseline, not end-to-end confidentiality from the storage provider. Optional client-side archive encryption requires a separately saved recovery key and a tested key-rotation/recovery design. Do not invent a cryptographic format in the first implementation or silently enable encryption without completing recovery-key setup.

A single archive does not protect against loss of its storage account. Independent second-copy replication must verify both content and recovery metadata, and keep credentials/retention independently administered. Replicating source deletions into every destination is not a backup strategy.

### A.9 Status and recovery evidence

Store separate timestamps for last source observation, last content change, capture completion, integrity verification, and restore test. Store their scopes: Git, LFS, wiki, individual point, or sampled repository.

A repository is **current and verified** only relative to the latest successful authorized source observation, the configured freshness threshold, and complete verification of all required components for the matching captured state. It is not a claim about unseen changes after that observation.

`status` exits successfully when the query works, even if it reports degraded protection. `status --check` has a documented nonzero health result for missing/stale required coverage. Keep configuration errors, authentication errors, conflicts, and operational failure codes distinct; machine clients use stable codes rather than English messages.

During restore, validate manifests and archive digests, reconstruct refs and HEAD, run Git integrity checks, and validate required LFS content. A recovery test must read the bucket into clean staging without quietly reusing the daemon's original repository. Report what was tested and what was excluded.

</details>

## Appendix B. TLA+ verification plan

<details>
<summary>Expand model scope, safety invariants, liveness assumptions, and implementation traceability</summary>

### B.1 Model the dangerous protocol, not the whole product

Represent objects by small abstract IDs rather than Git byte streams. Represent individual durable object writes, manifest publication, acknowledgment, crash/restart, duplicate delivery, restore pinning, and retention actions as separate transitions.

Suggested initial variables include `durableObjects`, `manifests`, `publishedPoints`, `verificationEvidence`, `acceptedJobs`, `jobOutcomes`, `hostedGeneration`, `acknowledgedPushes`, `retainedRoots`, `activeRestores`, `approvedPlans`, and `volatileState`. A crash clears volatile state, never assumed-durable storage.

The following are **proposed properties**, not executable or checked specifications:

```text
CompletePointIsClosed:
  Every published complete point has a durable manifest and all dependencies.

AcknowledgedPushIsDurable:
  At acknowledgment, the push's resulting refs and object closure are durable.
  They remain recoverable while retained or reachable from a retained root.

SourceLossDoesNotEraseHistory:
  Removing source access does not remove retained recovery roots.

ReplayIsConsistent:
  A repeated operation ID cannot commit conflicting terminal outcomes.

RetainedClosureSurvivesGC:
  Cleanup removes no object reachable from a retained point,
  current hosted generation, or active restore pin.

ApprovalMatchesExecution:
  An applied plan matches the approved actor, digest, scope,
  destination, expected versions, permissions, and validity window.

VerificationIsEarned:
  Verified status requires matching successful verification evidence
  for every required component; staging or upload alone is insufficient.
```

Notice the retention qualification on acknowledged pushes. The product must define how long an old, subsequently deleted ref is retained; a durability guarantee at acknowledgment is not an accidental promise of infinite storage.

### B.2 Assumptions must be visible

The core safety model assumes a single active writer, atomic visibility of individual completed object writes, the documented storage behavior actually used, and no spontaneous loss of acknowledged durable objects. Network responses may be lost or duplicated; the process may crash at any boundary.

Corruption, hostile administrators, hash collisions, and provider-wide data loss are not “proved impossible.” Detect corruption through independent checks, constrain hostile actions through security/retention controls, and handle account loss with an independent copy. Record these as boundaries of the claim.

Liveness is conditional: a valid job eventually reaches a reconciled outcome only if credentials remain usable, capacity is sufficient, required source data exists, the scheduler is fair, and dependencies eventually respond. Do not claim eventual backup during an infinite outage.

For finite models, use logical time with explicit timeout bounds. Approval expiry, retention age, and retry delays must not rely on unexplained real-clock ordering. Fairness choices and state constraints are reviewable specification inputs.

### B.3 Small models, then larger exploration

Start with one repository, two competing attempts, three to five objects, and two recovery points. Explore loss of response before and after publication, then add a second repository for job fairness and restore/cleanup interaction.

Check safety invariants, intended terminal states, and conditional liveness separately. Record the exact specification hash, TLC version, configuration, constants, state counts, runtime, and completion status. A timed-out exploration is incomplete, not a passing result.

Keep small bounded configurations in the pull-request suite; run larger configurations and randomized fault sequences nightly. Do not hide meaningful interleavings behind a state-space restriction simply to make CI green.

### B.4 Bridge specifications to code

| Formal action/property | Implementation boundary | Required executable test |
|---|---|---|
| Upload dependency | `ArchiveStore.put_verified` | Timeout after provider success; retry verifies the same content |
| Publish point | `CommitRecoveryPoint` | Crash immediately before/after publication; reconstruct from bucket |
| Durable job acceptance | `AcceptJob` | Lose SQLite after acknowledgment; accepted work remains discoverable |
| Acknowledge push | `CommitHostedGeneration` / Git response gate | Kill process at each boundary; every observed success restores |
| Retain or pin root | `PlanRetention` / `BeginRestore` | Cleanup races with restore; pinned closure remains readable |
| Apply approved plan | `ApplyPlan` | Change destination or policy revision after approval; execution denied |
| Record verification | `VerifyRecoveryPoint` | Missing LFS object prevents complete verified status |

For every counterexample, preserve the trace and create a regression test. Add property-based tests over operation sequences and replay implementation event traces against a model-level checker where practical.

Safety-sensitive reviews require both a storage/protocol reviewer and a model-aware reviewer. They must examine the abstraction gap: a model action described as atomic may actually contain several fallible I/O operations in code.

Formal modeling is a design and bug-finding discipline here. An implementation refinement proof is not part of the first release; do not imply otherwise.

</details>

## Appendix C. Performance engineering playbook

<details>
<summary>Expand benchmark fixtures, measurement protocol, CI gates, and reporting</summary>

### C.1 Reproducible scenario catalog

Generate synthetic fixtures from a fixed seed. Record actual packed bytes, logical content size, object/ref counts, compressibility, Git version, and fixture digest. Size labels below are nominal targets, not assumed measured outputs.

| Fixture | Shape | What it reveals |
|---|---|---|
| `empty-and-tiny` | Empty repository plus a few commits/refs | Fixed overhead, startup, correctness at boundaries |
| `git-100m` | About 100 MiB packed; 1,000 commits; branches and tags | Ordinary clone, capture, restore, and status |
| `git-1g` | About 1 GiB packed; mixed compressibility; deeper history | Pack handling, throughput, cache behavior |
| `git-10g` | About 10 GiB packed; many objects and refs | Peak memory/disk, streaming, cold restoration |
| `lfs-2g` | Small Git history plus eight 256 MiB LFS objects | LFS completeness, transfer, missing-object recovery |
| `portfolio-404` | 400 small repositories plus four larger ones | Discovery, fairness, scheduling, inventory cost |
| `history-rewritten` | Captured branches followed by force-push and deletion | Historical recovery and retention dependencies |
| `failure-matrix` | Interrupted uploads, lost responses, throttling, credential loss, corrupt objects | Retry amplification and truthful failure states |

For each applicable fixture, benchmark initial capture, no-change refresh, small incremental change, heavy change, warm clone, cold clone, verified restore, index rebuild, and process restart. Once writable hosting exists, add concurrent pushes and publication latency. Once cleanup exists, add retention/restore contention.

Use synthetic sources for deterministic PR tests. Test real GitHub and real storage providers with disposable repositories/buckets and least-privilege credentials, never the user's private portfolio by default.

### C.2 Measure boundaries explicitly

For backup, record queue wait, source observation, fetch, object validation, pack generation, upload, publication, and independent verification separately. For push, record client transfer, native Git validation, durable publication, and final acknowledgment. For recovery, record inventory rebuild, first useful output, download, repository reconstruction, and final integrity validation.

The report includes:

| Dimension | Metrics |
|---|---|
| Latency | Median/p95 where sample counts support them; cold/warm separated |
| Throughput | Input and output bytes per second with byte definitions |
| Resources | Daemon RSS, whole-process-tree peak RSS, CPU time, staging/cache peak disk |
| Storage overhead | PUT/GET/HEAD/LIST counts, retries, uploaded bytes, retained bytes |
| Amplification | Uploaded bytes per logical new content byte; request growth per changed repository |
| Recovery | Completion time, verified refs/LFS count, missing dependencies |
| Scheduling | Queue delay, stale repositories, starvation, retry count |
| AX | Tool-call count, response bytes, pagination, unauthorized-action rejection |
| Correctness | Integrity checks, recovered ref equality, and lost acknowledged updates |

Zero-change runs need an explicit absolute byte count, not a division by zero in amplification metrics. Highly compressible fixtures must not make throughput look better without disclosure. Do not call HTTP submission time “backup duration.”

Track request counts and bytes even without prices. A cost estimate may use a user-supplied, dated provider rate sheet; it is not a hardcoded promise about current cloud pricing. Report observed recovery-point age and measured restoration time separately: the recovery point objective (RPO) concerns potential uncaptured changes, while the recovery time objective (RTO) concerns time to regain usable data. A six-hour schedule alone establishes neither guarantee.

### C.3 Reference environment and controls

Maintain a pinned Linux runner class—for example, a fixed 4-core x86-64 allocation, 8 GiB RAM, and dedicated local SSD—with compiler, Git/LFS, kernel, filesystem, and build profile recorded. Report ARM separately; do not compare unlike architectures in the same regression series.

Separate microbenchmarks, local Git/storage emulation, network-shaped tests, and real-provider runs. Record bandwidth, RTT, TLS settings, region, endpoint, and concurrency. Include both loopback and a fixed network-shaping profile. A provider observation is not a deterministic gate until enough runs establish its variation.

Build baseline and candidate in clean worktrees with the same toolchain. Alternate their execution order. Reset mutated repositories and storage prefixes between repetitions; restore equivalent initial state outside the timed section unless setup itself is the scenario. Record exactly what “cold” means: application cache only, operating-system cache, remote data, or all applicable layers.

Begin with at least 30 repetitions for manageable end-to-end scenarios and larger sample counts for cheap latency paths; expensive cold/provider scenarios can use fewer runs with explicit uncertainty. Do not present p95 from a handful of full-portfolio restores as a reliable tail estimate. Preserve raw samples and confidence intervals.

Native Git over the same transport is the control for warm read performance. Native Git alone is not an equivalent control for durable object-storage pushes: report that added durability cost explicitly or compare against a control that performs equivalent persistence and verification.

### C.4 Regression policy

Use provisional thresholds until M1/M2 establish runner noise:

| Signal | Action |
|---|---|
| Any lost acknowledged update, broken closure, or unauthorized write | Block immediately; no performance waiver |
| More than 5% timing/resource regression | Warn and re-run on the pinned runner |
| More than 10% confirmed regression, above the metric's absolute noise floor | Block unless explicitly reviewed and accepted |
| Unexpected Git/LFS payload upload on unchanged input | Fail the semantic no-change benchmark |
| Deterministic storage-request count growth | Report every change; require rationale for material increases |
| Missing samples, failed setup, timed-out model, or invalid fixture | Mark incomplete/invalid, never green |

Timing confirmation requires a confidence interval supporting the regression, not just a worse single median. For fast CLI paths, begin with a 10 ms absolute noise floor and revise it from observed variance. Memory, request counts, and disk use need their own floors, not a shared millisecond rule.

Compare to both the merge base and the pinned release baseline so many individually small regressions cannot accumulate unnoticed. A waiver records the affected scenario, owner, explanation, correctness tradeoff assessment, expiry, and next target. Never combine results from incompatible fixture versions.

### C.5 The developer loop

The following project-owned commands are proposed tooling, not commands that exist today:

```sh
cargo xtask check                              # Formatting, linting, unit/contracts
cargo xtask model --suite pr                    # Pinned bounded TLC configurations
cargo xtask bench --suite quick --baseline main
cargo xtask bench --suite portfolio-404
cargo xtask fault-test --scenario archive-commit
cargo xtask perf-report --compare main
```

A typical change starts with an acceptance transcript and a benchmark case. A correctness-sensitive change also names its invariant and model action. The pull request includes before/after results from the same scenario, a trace or profile explaining material changes, and any remaining uncertainty.

A possible CI split:

```text
Pull request → unit/property tests → API/CLI contracts → small TLA+ models
             → deterministic benchmark smoke tests → pinned-runner comparison

Nightly      → larger models → crash/replay matrix → 404-repository scenarios
             → large/cold fixtures → isolated R2/S3/B2 canaries

Release      → fresh-machine install → bucket-only recovery → Git compatibility
             → security review → complete performance/cost evidence report
```

Keep the benchmark harness runnable locally and independently of GitHub-hosted CI. Retain benchmark reports outside the source repository as well as linking them from review; the disaster-recovery project should not lose all its development evidence with one host account.

### C.6 Report schema and review presentation

Version the project's result schema instead of coupling dashboards directly to one tool's internal output. Example fields:

```json
{
  "schema_version": 1,
  "record_kind": "illustrative_not_measured",
  "commit": "<candidate-sha>",
  "baseline_commit": "<baseline-sha>",
  "scenario": "portfolio-404/no-change",
  "fixture_digest": "<digest>",
  "runner_fingerprint": "<fingerprint>",
  "git_version": "<pinned-version>",
  "storage_mode": "emulated",
  "cache_state": "warm",
  "samples": 30,
  "metrics": {
    "elapsed_ms_median": null,
    "elapsed_ms_p95": null,
    "process_tree_peak_rss_bytes": null,
    "git_lfs_payload_uploaded_bytes": null,
    "storage_requests": null
  },
  "correctness": "not_run",
  "comparison": "not_run"
}
```

Each pull-request report shows only scenario, baseline, candidate, delta, uncertainty, and gate. Detailed samples, flame graphs, request traces, and environment manifests are one link away. The development dashboard should follow the same progressive-disclosure principles as the product.

For AX, start with deterministic scripted tasks instead of paid model evaluations: summary in at most two tool calls, bounded default response size, correct pagination, resumable job status, and denial of an unapproved plan. Later agent evaluations must record the model/client version and remain separate from protocol correctness tests.

</details>

## Appendix D. Repository shape and decisions to resolve

<details>
<summary>Expand the suggested workspace, design records, and bounded implementation spikes</summary>

### D.1 Suggested workspace

```text
gitpickle/
  crates/
    domain/                  # Context modules, value types, invariants
    application/             # Use cases, authorization, orchestration
    adapters/                # Git, GitHub, storage providers, SQLite
    interfaces/              # CLI, TUI, management HTTP, Git gateway, MCP
    gitpickle/                # Composition root and executable
  xtask/                     # Developer workflows and benchmark harness
  spec/                      # .tla/.cfg, assumptions, counterexamples
  benches/                   # Microbenchmarks and scenario definitions
  fixtures/                  # Seeded generators, digests, small test data
  tests/                     # Contracts, compatibility, recovery, security
  docs/
    product/                 # Journeys, copy, usability findings
    adr/                     # Architecture decision records
    archive-format/          # Independent recovery documentation
  perf/                      # Budgets, schemas, report templates
```

Use modules inside these crates until compilation, ownership, or dependency isolation justifies more crates. Avoid one crate per entity. Pin dependencies, generate an SBOM, verify distributed binaries, and test CLI/daemon version negotiation and archive-reader compatibility.

### D.2 Initial architecture decision records

Record the decision, alternatives, consequences, test evidence, and revisit trigger for:

| ADR | Initial direction | Revisit trigger |
|---|---|---|
| Deployment unit | One daemon, embedded database, single writer | Demonstrated availability or scale requirement |
| Git implementation | **Prefer integrated walgit crates**, with native Git tools where needed, behind narrow `GitEngine` adapter | walgit integration/provider/crash spike fails, or native alternative demonstrably simpler |
| Archive format | Immutable packs plus complete manifests | Measured amplification or restore cost violates budgets |
| Retention | No automatic deletion initially | Retention model and dependency-safe cleanup pass |
| Hosting modes | Mirror and writable hosting are separate | A modeled reconciliation policy is demanded by real usage |
| Interaction | Concise CLI + opt-in `gitpickle tui`, same application services | Evidence shows TUI harms reliability or requires duplicated logic |
| Deployment | VM/systemd and OCI/Coolify first; optional tailnet-only HTTPS | A genuinely serverless runtime meets job/Git/storage contracts |
| Agent integration | Local stdio; constrained tools; shared authorization | A concrete need for authenticated remote MCP |
| Language | Rust baseline | Prototype shows a simpler, maintainable alternative for the team |
| Name and brand | GitPickle as the working name; public use only after written permission from Software Freedom Conservancy | Permission refused; adopt RepoPickle (section 9) |

### D.3 Bounded spikes before commitments

The early spikes must answer concrete questions rather than grow into parallel products:

**walgit adoption / Git durability:** test walgit as the default engine in a small executable, then demonstrate correct final-response gating for push, failure rollback/isolation, and crash reconciliation on S3/R2/B2. This is the highest-risk implementation boundary. If it fails, document the alternative and its cost instead of silently weakening guarantees.

**Provider portability:** establish the exact operations and credentials needed on R2, S3, and B2, including retry ambiguity and takeover constraints. Do not accept “S3-compatible” as the test result.

**Archive economics:** compare full bundles, reusable pack sets, and bounded incremental strategies on the same fixtures. Choose the simplest representation that meets restore and request/byte budgets; measure repack-induced amplification.

**Recovery independence:** erase the daemon's working disk and restore using a separately saved recovery card. Verify key handling and archive-version compatibility.

**Deployment reproducibility:** exercise a pinned OCI image in Coolify and a versioned binary on a clean Linux VM; run private Tailscale Serve HTTPS and ordinary `git clone` without installing GitPickle on the client. Kill the daemon/cache and recover from bucket-only data.

**TUI comprehension:** test the 404-repository search/detail flow, contrast safe inspection with permission-gated actions, and ensure the same tasks work through the ordinary CLI.

**Onboarding comprehension:** test the guided flow with real users, including partial authorization, missing LFS, an offline server, and a nonexistent restore directory. Fix terminology before adding configuration options.

### D.4 Smallest credible success

The project succeeds first when a person can protect one private repository, understand what is covered, destroy the original server, and recover from the bucket with independent integrity evidence.

The 404-repository portfolio, normal Git hosting, and agent control scale that same contract. They must not introduce a second, less safe definition of “protected.”

</details>

## References

Primary documentation consulted on 14 October 2026. References support external facts and protocol/tool behavior; architecture, defaults, budgets, release gates, and command designs are proposals. Provider capabilities and protocol versions must be pinned and revalidated during implementation.

| Reference | Source | Relevance |
|---|---|---|
| [S1] | GitHub Docs — Backing up a repository | Git mirrors, LFS, and backup scope |
| [S2] | Nielsen Norman Group — Progressive Disclosure | Separation of primary and advanced interactions |
| [S3] | Git — git-http-backend | Native Git HTTP serving |
| [S4] | Model Context Protocol — Transports, 2026-07-28 | Stdio and Streamable HTTP transport model |
| [S5] | Model Context Protocol — Security Best Practices | MCP security boundary |
| [S6] | Domain Language — DDD Reference | Domain-driven design pattern vocabulary |
| [S7] | Amazon S3 — Conditional writes | Conditional object-write semantics |
| [S8] | Backblaze — S3-Compatible API | Provider-specific API behavior and restrictions |
| [S9] | Cloudflare R2 — Bucket locks | Provider-enforced retention controls |
| [S10] | Git — githooks | Receive hooks and post-receive limitations |
| [S11] | TLA+ Wiki — TLC Model Checker | Finite-state exploration and tooling |
| [S12] | Criterion.rs Documentation | Statistical Rust microbenchmarks |
| [S13] | Hyperfine project documentation | Command-line benchmark measurement and export |
| [S14] | Git — git-bundle | Portable full and prerequisite-based Git bundles |
| [S15] | Amazon S3 — Object API | ETag and checksum distinctions |
| [S16] | tobi/walgit — project README and MIT license | Git Smart HTTP/LFS, WAL, Rust crates, preliminary adoption candidate |
| [S17] | Coolify — Docker and Containers | OCI image, Compose, networking, state volumes |
| [S18] | Tailscale — Serve CLI | Tailnet-only HTTPS reverse proxy |
| [S19] | Tailscale — Enabling HTTPS | MagicDNS and tailnet certificate prerequisites |
| [S20] | Git — Trademark Policy | Restrictions on coined names that contain Git |

[S1]: https://docs.github.com/en/repositories/archiving-a-github-repository/backing-up-a-repository
[S2]: https://www.nngroup.com/articles/progressive-disclosure/
[S3]: https://git-scm.com/docs/git-http-backend
[S4]: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
[S5]: https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices
[S6]: https://www.domainlanguage.com/ddd/reference/
[S7]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html
[S8]: https://www.backblaze.com/docs/cloud-storage-s3-compatible-api
[S9]: https://developers.cloudflare.com/r2/buckets/bucket-locks/
[S10]: https://git-scm.com/docs/githooks
[S11]: https://docs.tlapl.us/using:tlc:start
[S12]: https://criterion-rs.github.io/book/
[S13]: https://github.com/sharkdp/hyperfine
[S14]: https://git-scm.com/docs/git-bundle
[S15]: https://docs.aws.amazon.com/AmazonS3/latest/API/API_Object.html

[S16]: https://github.com/tobi/walgit
[S17]: https://coolify.io/docs/core/docker-and-containers
[S18]: https://tailscale.com/docs/reference/tailscale-cli/serve
[S19]: https://tailscale.com/docs/how-to/set-up-https-certificates
[S20]: https://git-scm.com/trademark
