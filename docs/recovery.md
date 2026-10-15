# Storage-only recovery

`gitpickle restore --from-storage` reconstructs inventory from immutable archive records.
It needs no GitHub login, original server address, daemon database, or local cache.
Install native Git and Python 3 on the recovery machine. The executable embeds the version 1 reader.
You do not need a source checkout or Python packages.

Version 1 archives use no application encryption. Unknown encryption settings fail closed.
Provider encryption can require additional key permissions. Keep those recovery keys outside the original server.

## Recover from an archive directory

Use a downloaded archive prefix or a local archive directory:

```sh
gitpickle restore --from-storage /archives/pickle --inventory
gitpickle restore --from-storage /archives/pickle --repository repo123 --dry-run
gitpickle restore --from-storage /archives/pickle --repository repo123
```

The last command selects the newest complete verified point and creates a private bare repository.
The directory name contains the repository ID and point digest. Existing names receive a numeric suffix.
Use `--destination /new/recovered.git` to choose a directory. Its parent must exist.
Existing directories, files, and symbolic links are rejected. No overwrite option exists.

Use `--archive-id ID --root-digest SHA256` to check values saved on your recovery card.
These checks detect replacement of the archive root when you retain a trusted card.
Archive digests alone do not authenticate a malicious replacement of the entire archive.

## Recover directly from storage

Supply read credentials through the runtime environment:

- `GITPICKLE_STORAGE_ACCESS_KEY`
- `GITPICKLE_STORAGE_SECRET_KEY`
- `GITPICKLE_STORAGE_SESSION_TOKEN`, when required

Use the endpoint, bucket, region, and prefix from your recovery card:

```sh
gitpickle restore --from-storage --provider r2 \
  --endpoint https://ACCOUNT.r2.cloudflarestorage.com --region auto \
  --bucket private-git --prefix archive/ --inventory
```

Replace `--inventory` with `--repository repo123 --dry-run` to preview recovery.
Remove `--dry-run` to recover. The same options accept `--provider s3` or `--provider b2`.
Use the provider's signing region. See [provider configuration](storage-providers.md).

Recovery calls only storage listing and reads. It follows every listing page, including empty pages with continuation tokens.
It checks object digests independently of provider entity tags.
No writer lock, writable takeover, original DNS record, or archive write permission is required.

## Selection and validation

The inventory includes committed points, component coverage, missing dependencies, and matching verification evidence.
It ignores local indexes, cache files, and unpublished points.
The default selects the last complete verified point in publication order.
A successful upload or publication alone does not establish verification.
Evidence must match the archive, repository, point, reader version, and required components.
Conflicting or damaged publication chains stop automatic selection.
Corruption stops recovery instead of silently selecting an older point.

A preview returns versioned JSON, expected bytes, refs, HEAD, missing data, and the destination.
It checks archive digests. Its `git_integrity: not_run` field distinguishes a preview from a restore test.
It does not create the destination or write archive state.

Execution initializes a bare repository in private, clean staging.
It validates dependency digests and sizes, pack contents, every recorded Git object, refs, and HEAD.
It checks historical Large File Storage (LFS) pointers against the manifest.
It runs `git fsck --full --strict` before exposing the destination.
Inherited Git state, replacement objects, global configuration, hooks, and network transports are disabled during recovery.
Failure removes staging and leaves the destination absent.
Process interruption can leave temporary staging. Such directories do not count as completed recovery.

## Select historical or incomplete data

Copy the exact point key from the inventory:

```sh
gitpickle restore --from-storage /archives/pickle \
  --point repos/repo123/points/DIGEST --destination /new/historical.git
```

Explicit point selection can recover an independently readable manifest when its publication chain is damaged.
It does not assume previous verification. Execution still checks integrity.

Add `--allow-incomplete` only with `--point` to recover incomplete data.
The preview and result include missing-data warnings and component coverage.
Missing LFS can produce a Git repository with `complete: false` and an explicit list of missing LFS identifiers.
Missing Git dependencies, invalid refs, or failed Git integrity still stop recovery.
Corruption is never treated as an allowed omission.
The capture writer retains Git progress when required LFS or an enabled wiki is unavailable.
Such points record incomplete component coverage.

## Export a full Git bundle

```sh
gitpickle restore --from-storage /archives/pickle --repository repo123 \
  --bundle --destination /new/export
```

The new directory contains `repository.bundle` and `export.json`.
The bundle is full and has no Git prerequisites. Verification runs in an empty repository.
The metadata records the exact refs, symbolic or detached HEAD, coverage, and LFS exclusion.
Git bundles contain Git data only. Use bare repository recovery to recover required LFS bytes.
The full label refers to Git prerequisites, not complete LFS or wiki coverage.
An empty repository cannot form a Git bundle. Export it as a bare repository instead.
Prerequisite-based bundle export is not offered.

## Current limits and validation

The Rust adapter embeds the existing reader to keep one archive validation implementation.
Python 3 remains a runtime prerequisite. Recovery does not import code from the working directory.
Porting the reader must retain the same archive-only acceptance tests.

Provider recovery currently downloads the whole archive prefix into a temporary directory before selection.
It loads individual objects into memory. Budget disk for the archive, staging, and destination.
This implementation does not establish full-size or 404-repository performance.
The small local capture benchmark also measures archive-only reconstruction; see the archive format documentation.

`cargo xtask check` exercises local correctness without provider credentials.
Tests delete source repositories, indexes, and locks, block source network access, and recover historical refs.
They also verify incomplete selection, failed evidence, corruption, safe destinations, and full bundle export.
An installed binary test runs outside the source checkout.
A local storage server exercises paginated recovery through all three provider adapters using only reads.
These tests establish deterministic behavior. Live provider and fresh-machine release checks remain separate gates.

## LFS and wiki components

Bare recovery installs verified LFS objects into `lfs/objects` and an enabled wiki into `wiki.git`.
The wiki is a separate bare repository with its own refs, HEAD, integrity checks, and required LFS objects.
Restore its immutable point directly when you need only the wiki.
The result reports `verified_components` and a separate `wiki` result.
Default selection excludes points with incomplete LFS or wiki coverage.
Explicit incomplete recovery preserves Git and reports missing LFS objects and component coverage.
Corrupt archive bytes always fail validation and leave the destination absent.
A full Git bundle excludes LFS and wiki content. Use bare recovery for those components.

## Save a recovery card and software

Protection setup returns a `recovery_card` in its result, including with `--wait`.
Save the card outside the daemon and the archive account. Keep it current when storage locations change.
It contains no credential or decryption material. Treat bucket names and paths as private operational information.
The filesystem adapter records its archive path instead of a bucket and prefix.

Regenerate the card from a local archive:

```sh
gitpickle restore --from-storage /archives/pickle --recovery-card > recovery-card.json
```

For remote storage, use the same provider options shown above and replace `--inventory` with `--recovery-card`.
The command reads storage with your runtime credentials. It never writes archive data.
It currently downloads the full prefix, as other provider recovery commands do.
Save the raw card, or use `--json` and save the `data` field from the result envelope.

The card records the archive identity and the SHA-256 digest of `archive.json`.
Pass these as `--archive-id` and `--root-digest` during recovery.
Verify these values against your separately retained card before trusting an unfamiliar archive.

Keep an executable for your recovery platform, its trusted checksum, and a source copy outside the original machine.
Also retain `Cargo.lock`, the pinned toolchain, dependency sources or an offline build cache, and the archive format documentation.
Keep native Git and Python installation packages when recovery must work without public download services.
The card describes this retrieval path. Public release hosting is not required and is not assumed to remain available.
Test the retained executable on a separate machine. A checksum from the same untrusted download cannot establish authenticity.
A source copy alone does not ensure that its compiler and dependencies remain available.
Keep software copies separate from recovery keys and storage credentials.

Version 1 requires no application recovery key. Client-side archive encryption is unavailable.
The reader rejects encrypted archives, including unknown encryption settings.
Provider-managed encryption can still require access to provider keys and their accounts.
Record that access in your separate recovery vault and test it independently.
A provider key available only through the lost server cannot satisfy independent recovery.
No client-side encryption may be added until key retention, rotation, loss, and fresh-machine recovery have separate tests.

Recovery cannot recreate data that was never captured or no longer exists in storage.
Unpushed commits, dirty files, inaccessible refs, uncaptured LFS, and unavailable wikis remain outside retained coverage.
GitHub issues, pull requests, releases, packages, and account settings are not included.
Loss of the bucket account, required provider keys, or every independent software copy can prevent recovery.
A recovery card does not provide a second archive copy or enforce provider retention.
Source capture after recovery can require new GitHub authorization; storage-only restore does not.

## Compatibility policy and evidence

| Interface | Supported behavior | Failure behavior |
|---|---|---|
| Archive records | Version 1; additive fields are ignored | Unknown versions or encryption fail before recovery |
| Verification evidence | Reader version 1 with matching point and components | Other evidence cannot establish default verified selection |
| Management protocol | Version 1 | Missing or unsupported selection is rejected before dispatch |
| Machine results | Version 1 | No compatible schema stops the client before a command |
| Software package version | Diagnostic; client and daemon can differ | Package version differences alone do not reject a command |
| Capabilities | Only implemented operations are advertised | Missing required operations stop the client |

The authenticated `hello` operation advertises software version, management and result ranges, and available operations.
The client selects version 1 when both ranges include it.
Each command carries `X-GitPickle-Management-Version: 1` and `X-GitPickle-Result-Version: 1`.
The daemon validates both headers before dispatch. Authentication and scoped authorization still apply.
Older clients that omit these headers must be upgraded before using this daemon.
Archive recovery has no management handshake and requires no daemon connection.

`fixtures/archive-v1` retains serialized version 1 bytes for a reader compatibility test.
Do not regenerate it with a changed writer. Add another retained fixture when a format version changes.
Tests also exercise current-writer Git, LFS, wiki, historical, and storage-only recovery.
Transport tests reject incompatible ranges and missing capabilities before application dispatch.
They accept a different software version when protocol ranges overlap.
Live S3/B2 and fresh-machine deployment checks remain final validation gates.
