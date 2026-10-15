# Operate GitPickle

GitPickle is the development working name. Public naming and release acceptance remain pending.
Start with the [release scope and evidence](release/README.md), then check the [compatibility matrix](compatibility.md).
Protected mirrors reject pushes. Explicit hosted mode supports durable pushes and LFS uploads.
See [hosting](git-hosting.md) for grants, conversion, reconciliation, and tested limits.
The [implementation acceptance record](implementation-acceptance.md) separates local evidence from final gates.

## Install and protect

1. Build and verify the [deployment bundle](deployment.md). Retain its checksum and offline runtime dependencies.
2. Use [guided setup](setup.md) for a local server, SSH installation, or a generated Coolify recipe.
3. Configure a dedicated service account, private policy, runtime credentials, and one archive writer.
4. Review authorized repositories with [guided protection](cli-workflow.md). Confirm scope before applying the plan.
5. Save the [recovery card](recovery.md#save-a-recovery-card-and-software) outside the daemon and archive account.
6. Inspect `gitpickle status` and `gitpickle doctor --all`. Check required Git, LFS, and wiki coverage separately.
7. Restore a selected point to a new directory. Retain the result, exact point, timestamp, and component checks.

Git Large File Storage (LFS) bytes require separate capture. An enabled wiki is a separate repository.
Authorization can hide repositories from discovery. “Authorized repositories found” does not establish account-wide coverage.
Unpushed commits, dirty files, inaccessible refs, and data deleted before capture are outside coverage.
An unchanged source can be fresh while its recovery point is old.
A completed job does not establish verified coverage. A sampled restore covers only its selected point and components.

## Routine checks and incidents

| Observation | What remains | Required action |
| --- | --- | --- |
| Source access fails or freshness expires | Retained points remain available | Check source permissions and connectivity; inspect the last complete point. |
| Required LFS or wiki data is missing | Captured Git can remain available | Restore source access and retry capture. Do not label required coverage verified. |
| Storage fails during capture | Earlier publications remain; the new outcome can be unknown | Restore storage access and inspect the durable job before retrying with its original identity. |
| Archive integrity fails | Other data can remain, but the selected point is untrusted | Stop mutation. Retain evidence and investigate storage or an independent copy. |
| Two publication chains conflict | Explicit points can remain recoverable | Stop writers. Inspect ownership; never choose a winner by timestamp. |
| An approved restore stops after consumption | Destination effects can have an unknown outcome | Inspect the destination before preparing a new plan. Do not replay the consumed plan. |
| Disk capacity is low | Existing archive data remains | Stop new work if needed. Add capacity; do not delete retained archive dependencies. |

Use [jobs](jobs.md) for durable acceptance and cancellation semantics.
Closing a client does not cancel accepted work. Cancellation cannot undo a committed recovery point.
Use [status](status.md) for timestamps, evidence scope, and machine result codes.
Use the [terminal interface](tui.md) or [local agent tools](mcp.md) only with the same scoped grants.

## Recover without the server

1. Retrieve the independent recovery card, verified executable, native Git, Python, storage credentials, and required provider keys.
2. Use the [storage-only recovery procedure](recovery.md) to inventory the archive.
3. Compare the archive ID and root digest with the separately retained card.
4. Preview the selected point and destination. Budget disk for the full archive download, staging, and destination.
5. Restore to a new directory. Require exact refs, Git integrity, and the required LFS/wiki checks.
6. Retain the result independently. Reconnect source credentials only when resuming capture.

Recovery needs no original daemon database or GitHub login.
Version 1 has no application encryption. Provider key access can still be required.
Unknown archive versions or encryption settings fail closed.
A complete Git bundle excludes LFS and wiki data. Use bare recovery for those components.
See the [archive specification](archive-format/README.md) for records and independent reader behavior.

## Replace a writer or upgrade

Stop and isolate the old writer before starting a replacement.
Keep a persistent server identity across restarts. Never copy that identity to a second active writer.
A different server opens an existing archive read-only until explicit takeover.
Follow [writer ownership](archive-format/README.md#writer-ownership), including credential fencing and exact publication reconciliation.
A local lock or timeout cannot fence another server.
Verify bundle checksums and archive compatibility before an upgrade or rollback.
Retain the previous executable and dependency packages; rollback is safe only when that reader supports the archive.

## Protect access

Keep management private. Expose only `/git` through the configured private HTTPS proxy.
Tailnet access does not replace repository authorization. Disable public Funnel.
Rotate runtime credentials and restart to reload tokens and policy.
See [security boundaries](security.md) for container binding, process limits, and trusted identities.
Application append-only behavior does not enforce provider retention.
Account loss, hostile administrators, or lost provider keys can defeat a single archive copy.
Automatic retention cleanup and second-copy replication are unavailable.
