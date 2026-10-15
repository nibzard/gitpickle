# Development test resources

T004 verifies R2 and GitHub access on 15 October 2026.
These resources are authorized for synthetic development fixtures only.
The owner supplies the persistent R2 bucket. Each operation owns a unique prefix.
Disposable GitHub repositories are removed by Looper cleanup.
Never treat a repository from a completed attempt as an existing fixture.

## Resource handoff

| Field | Verified value |
| --- | --- |
| Cloudflare account | `24f97aae6afe0315dac069a5c08fe795` |
| R2 endpoint | `https://24f97aae6afe0315dac069a5c08fe795.r2.cloudflarestorage.com` |
| Region | `auto` |
| Persistent private bucket | `gitpickle-dev-tests` |
| Authorized base prefix | `gitpickle-development/` |
| GitHub account | `nibzard`, user ID `6250945` |
| GitHub permissions available | `repo`, `delete_repo`, `gist`, `read:org`, `workflow` |
| Runtime credential file | `/home/agent/.config/gitpickle/r2-test.env`, mode `0600` |
| Owner access evidence | `/home/agent/.config/gitpickle/r2-access-check.json` |
| Generated operation evidence | `tests/results/resources/t004-preflight.json` |

The runtime file supplies `GITPICKLE_STORAGE_CONFIG` as JSON and two credential variables:
`GITPICKLE_STORAGE_ACCESS_KEY` and `GITPICKLE_STORAGE_SECRET_KEY`.
Source the file only in the shell for authenticated operations.
Never print its contents, credential variables, signed headers, or SDK debug logs.
No credentials belong in the repository or reports.
The helper requires Python 3, Boto3, the Cloudflare CLI, and the GitHub CLI.
Boto3 is already available on this machine. It is optional for normal local checks.
The helper does not install packages or change repository configuration.

The configured Cloudflare identity matches the endpoint account.
The bucket exists. Its managed public domain is disabled, and it has no custom domains.
The GitHub identity has the permissions required to create and delete private fixtures.
The owner-provided R2 credentials pass signed object operations.
Their server-enforced permission scope is not independently introspected.
The helper restricts operations to the exact bucket and a unique object prefix.
It does not use account administration credentials for object access or create API tokens.

## Permitted scope

- Use only synthetic Git, Git Large File Storage (LFS), and supported wiki fixtures in task-owned private repositories.
- Permit fixture commits, branches, tags, history rewrites, and source deletion tests only within those repositories.
- Store synthetic objects and multipart uploads only under each operation's unique authorized prefix.
- Register separate cleanup for each object or multipart upload before creating it.
- Never change bucket settings, enable public access, apply retention locks, or delete the persistent bucket.
- Never read or mutate unrelated repositories, objects, prefixes, or buckets.

The provisioning helper creates an empty private repository and one small R2 probe object.
It verifies listing and exact byte readback, then deletes the owned probe object.
It does not test Git transport, LFS transfer, wiki capture, or the complete storage contract.
The created private fixture reports `has_wiki=false`; usable wiki access is not established.
Do not claim wiki coverage from repository creation alone.

The command-line credentials have broader permissions than each fixture needs.
Do not use these credentials as a template for deployed application credentials.
See [storage provider permissions](storage-providers.md) for the application requirements.

## Repeat provisioning

Run from the repository root in a Looper operation.
Looper supplies `LOOPER_RESOURCE_PREFIX`, `LOOPER_RESOURCE_CLEANUP_FILE`, and `LOOPER_ARTIFACT_DIR`.
Use a new operation prefix for T007. Do not reuse this attempt's object prefix.

1. Confirm the runtime credential file exists with mode `0600`.
2. Run the helper in an authenticated shell:

   ```sh
   (
     set +x
     source /home/agent/.config/gitpickle/r2-test.env
     python3 tools/gitpickle_test_resources.py provision
   )
   ```

3. Read the nonsecret report at `tests/results/resources/t004-preflight.json`.
4. Use its exact resource identifiers only within the permitted scope.
5. Let Looper run the registered cleanup after the operation.

The helper verifies identity, private access, required GitHub permissions, and name availability before creation.
Names contain `LOOPER_RESOURCE_PREFIX` and a random suffix.
Before the first mutation, it appends cleanup to the existing cleanup file and flushes it to disk.
A receipt and a copy of the cleanup runner remain outside the checkout in `LOOPER_ARTIFACT_DIR`.
Recovery therefore does not require this worktree.
The generated report is also copied there for the handoff.
Generated reports and the Cloudflare CLI account cache stay ignored. Setup and verification check that tracked and unignored files remain unchanged.

Cleanup checks repository owner ID, privacy, name, description marker, creation time, and recorded repository ID.
For objects, it checks the exact key, ownership metadata, and SHA-256 content digest before deletion.
It deletes no other keys. Missing resources are successful cleanup results.
Changed ownership or content stops removal and retains the receipt for recovery.
Only one operation may write each unique key; this basic probe does not establish atomic conditional deletion.
Interrupted operations retain the registered cleanup and receipt until recovery runs.
The bucket is persistent and always stays outside resource cleanup.

For manual recovery, use the runner, receipt, and resource prefix recorded in the report.
Run it in the same authenticated shell:

```sh
python3 /absolute/artifact/t004-cleanup-SUFFIX.py cleanup /absolute/artifact/t004-ownership-SUFFIX.json ORIGINAL_RESOURCE_PREFIX
```

Use the exact recorded values. Do not substitute a different operation's prefix.
After adding further fixtures, preserve these registrations and add ownership checks for each new resource.

## Acceptance evidence

At `2026-10-15T14:04:19Z`, the live probe passes signed R2 list, write, readback, and owned-object deletion.
Repeated object cleanup succeeds, and the operation prefix is empty afterward.
The private repository `nibzard/gitpickle-looper-4a373f2eb295aece07c8-13755f48e41e` is created with ID `1420826839`.
It has administrator access and no branches at verification time.
Looper removes it after this operation; later tasks must recreate their own fixtures.
Its R2 operation prefix is `gitpickle-development/looper_4a373f2eb295aece07c8/t004-13755f48e41e/`.
The bucket remains available for T007 with new operation prefixes and the same runtime configuration.

An initial probe encounters an older Python SDK model that rejects conditional request fields.
That attempt creates no R2 object. Its task-owned repository is deleted, and repeated cleanup succeeds.
The repaired probe uses ordinary writes in its unique prefix.
The Rust adapter and T007 remain responsible for conditional-publication verification.
Six focused cleanup tests pass, including foreign object metadata, changed content, foreign repository markers,
invalid keys, exact deletion scope, and repeated cleanup.

Retained T004 evidence records an earlier GitHub scope blocker and a Cloudflare token-creation denial.
These are superseded by the current `delete_repo` permission and owner-provided R2 credentials.
The earlier disposable resource `gitpickle-looper-656d48640778c7640a20-876d31f74b` has a retained cleanup receipt.
A fresh GitHub lookup returns HTTP 404. That repository is not part of the handoff.
The owner's earlier signed list/write/readback/delete evidence also records completed object cleanup.

Run the focused local safety checks without external mutations:

```sh
python3 -m unittest discover -s tools -p test_gitpickle_test_resources.py
```

The complete `cargo xtask check` passes, including all 172 Python acceptance tests.
Its log is retained as `t004-check.log` in the operation artifact directory.
Source digests match before and after verification.
The workflow fixture now has its own Git boundary without a remote.
This prevents it from inheriting the project remote; all existing scope assertions remain in place.

This evidence establishes development access only.
T007 must run R2 conformance, including multipart, ambiguous responses, conditional publication, takeover, and retention checks.
T053 and T054 retain the final S3 and B2 prerequisites and acceptance gates.
No S3 or B2 resource or conformance result is claimed here.
