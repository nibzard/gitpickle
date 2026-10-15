# Durable jobs and bounded scheduling

`tools/gitpickle_jobs.py` implements jobs for the local archive reference service.
It uses the existing `Writer` and capture adapter. It needs no third-party Python packages.
The Rust transport and daemon integration belong to the interface implementation tasks.
This evidence covers local filesystem durability, not cloud provider behavior.

## Durable authority

One `Jobs` session owns the archive writer lock until all active workers stop.
A second process cannot schedule mutations concurrently. External fencing remains required for takeover.

Job IDs hash the actor, operation, and idempotency key as canonical JSON.
Arguments include the repository, source descriptor, local source path, and staging reservation.
The service rejects changed arguments under the same ID with `conflict`.
Different actors and operations have independent keys. Acceptance retries return the original job.
Callers must supply authorized actor identities. This adapter does not grant permission.

Records live at `jobs/<job-id>/events/<digest>`.
Each record has `kind: job`, `version: 1`, `archive_id`, `job_id`, `parent`, `event`, and `value`.
The value contains the immutable request, acceptance sequence, acceptance time, state,
attempt count, next eligible time, source cooldown, cancellation request, result, and error code.
`parent` references the preceding record, or null for acceptance.
Events are `accepted`, `started`, `retry`, `cancel_requested`, and `terminal`.
Terminal states are `completed`, `failed`, and `cancelled`.
Readers reject conflicting chains, changed arguments, unsupported versions, and corrupt digests.

Every transition flushes an immutable record, reads it back, then updates SQLite.
Acceptance and terminal replies follow that sequence. Ambiguous writes reconcile by exact bytes.
Restart lists and validates archive records, then recreates `.projection/jobs.sqlite3`.
Missing or corrupt SQLite cannot lose accepted work or change terminal outcomes.
Attempt and retry records survive local disk loss. Progress updates need no durable record.
No record contains exception text. Keep source descriptors and local paths free of secrets.
The reference adapter accepts local source paths or a numeric GitHub ID.
The GitHub adapter resolves IDs and supplies credentials only at execution time.
See [GitHub protection](github-protection.md) for discovery, scope, and scheduling.
`backup` publishes a recovery point; `sync` records source refs without a capture.

## Execution and resource limits

Call `start()` from the server to run the scheduler independently of clients.
Call `accept()`, `inspect()`, and `follow()` from client threads.
`follow()` catches client interruption and returns `detached`; server work continues.
Only `cancel()` requests cancellation. Server shutdown stops admission and waits for active workers.
Queued work remains in the archive for restart.

The scheduler rotates repository queues. Each repository has at most one active job.
Global concurrency bounds the worker pool. A large repository cannot occupy every worker.
The source interval limits job starts per source. Rate-limit errors add a source cooldown.
The source adapter must enforce request-level limits inside each capture.
Retryable failures use capped exponential delays with jitter between half and all of the delay.
A provider retry delay sets a lower bound. Authentication and integrity failures are terminal.
Retries stop at the configured attempt limit. Time values use Unix seconds in UTC.
These rules assume a reasonably synchronized server clock after restart.

Admission subtracts active staging reservations from current free disk space.
It preserves the configured disk reserve and leaves inadmissible jobs queued.
Set each reservation from a conservative capture estimate. An unknown capture size needs an operator budget.
Admission does not guarantee that an upstream repository stops growing during capture.
The last committed point is never deleted, including after disk exhaustion.

Workers check cancellation before source access. Native Git capture runs to its next safe boundary.
An active capture can finish before cancellation takes effect.
A committed capture always wins over cancellation, including after process death.
Replay uses the stable job ID as the capture operation ID.
It reconciles a prior publication before accessing the source or checking cancellation.
An interrupted job can therefore finish without the original source.
Terminal writes never remove recovery points. Archive deletion remains disabled.

## Model mapping and verification

| Model action | Implementation and fault boundary |
| --- | --- |
| `Accept`, `AcceptReply` | `accept`, `_append`; `before_accepted`, `after_accepted` |
| `Deliver`, `Reject` | `tick`, `_execute`, scoped argument comparison in `accept` |
| `Commit`, `TerminalReply` | `_finish`, `_append`; `before_terminal`, `after_terminal` |
| `Crash` | `_rebuild`, capture reconciliation using the stable job ID |

`test_duplicate_requests_scope_and_argument_conflict` exercises duplicate delivery and argument conflict.
`test_accept_and_terminal_crash_boundaries_and_sqlite_loss` checks both acknowledgment boundaries.
`test_real_process_exit_after_acceptance_and_capture` kills processes after durable transitions.
`test_interrupted_capture_reconciles_before_cancellation` covers the conflicting-attempt model regression.
It preserves one published outcome after a crash, source loss, and cancellation.
Other tests cover actual concurrent captures, lost responses, bounded staging admission,
source cooldowns, durable backoff, attempt limits, safe cancellation, and client detachment.
The fairness test queues work for 404 repositories behind repeated large-repository requests.

Run the acceptance tests and the complete workspace checks:

```sh
python3 -B -m unittest discover -s tools -p test_gitpickle_jobs.py -v
cargo xtask check
```

Generated archives and databases stay under ignored `fixtures/generated/` during tests.
Tests use `LOOPER_RESOURCE_PREFIX` for private temporary directories.
They create no external services or cloud resources.
