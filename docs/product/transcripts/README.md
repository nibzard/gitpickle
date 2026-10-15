# Acceptance transcripts

These are normative scenarios, not output from a working daemon.
`contracts.json` contains machine counterparts checked by `cargo xtask check`.
The mirror rejection entry represents the management error underlying the Git rejection.
It is not JSON emitted by `git push`, and its exit value is a management exit value.

## Protect authorized repositories

```text
$ gitpickle protect github
Run protection on: backup@my-server
Store recovery points in: R2 / private-git
404 authorized repositories found.
Include future repositories within this authorization? [y/N] y
Plan: capture every 6 hours; discover daily; warn after 8 hours.
Capture Git, required LFS, and enabled wikis. Keep retained points.
GitHub metadata: not included. Hosting: off.
Local remotes and uncommitted files remain unchanged.
Apply this plan? [y/N] y
Job job_1 accepted durably. 404 repositories pending.
Next: gitpickle jobs get job_1
```

Given successful prerequisite probes and a trusted approval, acceptance returns a durable ID.
Repeating the same actor, operation, key, and arguments reuses the accepted job.
Different arguments with that key return `conflict`. Setup resumes without creating another bucket or service.

## Status and incomplete coverage

```text
$ gitpickle status
Scope: all 404 registered repositories
402 current and verified; 2 need attention.
Source observed: 2026-10-14 12:00 UTC
Independent restore test: 1 repository passed at 2026-10-13 12:00 UTC.
alex/media: Git retained; 2 required LFS objects missing.
alex/archive: source unavailable; last retained point is 3 days old.
GitHub issues, pull requests, and release assets: not included.
Next: gitpickle doctor --attention
[exit 0]
$ gitpickle status --check
[Same scope and coverage; exit 6]
```

An unchanged repository with fresh observation remains current when its matching point has complete verification.
An unavailable source remains at risk even when older points are retained.
Missing LFS or enabled wiki content cannot become `verified`. Recovery can still expose retained Git-only progress.
The JSON fixtures cover successful, degraded, and health-check responses.

## Recover without the server

```text
$ gitpickle restore --from-storage
Archive: archive_1
Access: storage credentials and configured recovery keys
Recover: alex/project
Point: point_1; 2026-10-14 12:00 UTC; Git, required LFS, and wiki verified
Destination: ./recovered/project
Existing files overwritten: none
Restore? [y/N] y
Job job_2 accepted durably.
Git integrity, required LFS digests, and wiki integrity passed.
Evidence: restore_evidence_1; repository repo_1; point point_1.
```

Run future end-to-end acceptance with the old database and cache removed and GitHub access blocked.
No original-server DNS or control-plane account is required.
An existing destination fails safely unless a separately approved overwrite plan permits it.
An incomplete point is excluded by default. Explicit selection lists missing components before approval.
A corrupt dependency returns `integrity_failure`; it cannot produce a verified result.

## Hosting modes

```text
$ git clone https://git.example.com/alex/project.git
[Authenticated clone from a committed protected-mirror generation]
$ git push origin main
remote: Protected mirror: pushes are read-only. Create an authorized conversion plan.
[Git reports rejection]
```

After writable-hosting gates pass, conversion creates a checkpoint and pauses source updates.
The approved hosted repository accepts ordinary authenticated pushes.
A successful push appears only after its closure and generation are durable in the archive.
Loss of the response after publication requires reconciliation by operation identity.
A storage failure yields no final success. A background source sync returns `conflict` in hosted mode.
Resuming mirroring requires explicit reconciliation; it cannot silently overwrite hosted refs.

## Automation and negotiation

```text
$ gitpickle backup --json < /dev/null
{"schema_version":1,"outcome":"failed","error":{"code":"invalid_input"}}
[Abbreviated envelope; full fields follow contracts.md. No prompt. Exit 2.]
```

A noninteractive request with missing scope never prompts or expands to all repositories.
Progress stays on stderr. `--json` never starts the terminal interface.
Incompatible protocol ranges return `unsupported_version` before any job is accepted.
Missing hosted capability returns `unsupported_capability`; it cannot enable writes implicitly.
