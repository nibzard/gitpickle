# R2 storage verification

T007 exercises the Rust R2 adapter against the owner-provided `gitpickle-dev-tests` bucket.
The authorized base prefix is `gitpickle-development/`. This is synthetic test data only.
This evidence applies to R2.
Actual Amazon Simple Storage Service (S3) and Backblaze B2 verification remains mandatory in T054 after implementation.
T053 supplies those providers' test access. Local adapter tests do not replace that gate.
The probes use Hypertext Transfer Protocol (HTTP) and Secure Hash Algorithm 256 (SHA-256) digests.

## Repeat the verification

The runtime handoff and permitted scope are in [test resources](test-resources.md).
Run from the project root in a Looper operation with the existing Rust toolchain and Boto3 installed.
The Cloudflare command-line interface must have permission to read this bucket's lock rules.
The runner requires an empty lock configuration. It never changes that configuration.

```sh
(
  set +x
  source /home/agent/.config/gitpickle/r2-test.env
  python3 -B tools/gitpickle_r2_conformance.py run
)
```

The runner performs these steps:

1. Validate the exact authorized endpoint, bucket, base prefix, and resource prefix.
2. Read lock rules, build the Rust example, and reject an occupied random operation prefix.
3. Save the receipt and cleanup runner outside the checkout. Append and flush cleanup registration before mutation.
4. Run `conformance::run` through the real signed HTTP transport, then run the separate multipart and response-loss probes.
5. Save nonsecret request evidence, including statuses, write digests, and discarded-response markers.
6. Run ownership-checked cleanup twice. Keep the registration for Looper recovery.
7. Confirm tracked and unignored source files have the same modes and digests as before verification.

Generated output is ignored under `tests/results/storage/`.
`r2-conformance.json` contains the result, operation identifiers, and retained artifact paths.
The retained journal records method, key, query, body size, digest, response status, and fault marker.
It excludes credentials, signed headers, and object bytes.
A write intent records proposed content, including writes that fail their condition. It does not certify stored content.
The independent digest readback checks certify the bytes used by each probe.

Cleanup uses only exact journaled keys under this random operation prefix.
Before deletion, it requires the stored content to match a journaled write digest.
An unfinished upload must match a journaled initiation key and a recorded identifier or the operation's creation interval.
Unknown objects, older uploads, changed content, and invalid receipts stop cleanup and retain evidence for recovery.
No bucket or unrelated key is deleted. A truncated journal also stops cleanup.
The registration sources the runtime credential file and uses an artifact copy of the cleanup code.
Interrupted work therefore remains recoverable without this checkout.
Run the registered command with its original receipt and resource prefix for manual recovery.

## Observed behavior

The final live run starts at `2026-10-15T14:22:34.928503+00:00` and completes its probes at `2026-10-15T14:22:50.400611+00:00`.
Its operation identifier is `t007-371d2bfaf21c4f5ba423af61`.
The journal contains 50 provider responses: 37 HTTP 200, two HTTP 412, eight HTTP 404, and three HTTP 204.
Two successful responses are deliberately discarded before the adapter receives them.
The journal SHA-256 is `644d3b153ad409fa0290674a4633e56c3bc4651e18df182477742b33768402ef`.
The multipart payload SHA-256 is `e62482a429f71adbbf1835c99e232df4f874f3d99c87151c4cc44d6b0cb7a559`.
The ambiguous PUT payload SHA-256 is `f9b823472952f6c88ea527998bc238eb8392830099daca3bdb10b171f3e515ff`.
The retained result is `/home/agent/.looper/gitpickle-c39a4efa/runtime/task-db4686c8c6df4514/work-00-session-9dfbf4829b25d2c1/t007-371d2bfaf21c4f5ba423af61-result.json`.
The sibling `.jsonl` journal and `.json` cleanup receipt retain the exact operation scope.
Both live attempts pass and clean up their owned resources twice.

The live run on 15 October 2026 passes the following checks:

| Check | Evidence and limit |
| --- | --- |
| Completed-write visibility | Three conditional PUTs are immediately readable and listable. |
| Listing | Page size one enumerates all three keys through continuation tokens without duplicates or omissions. |
| Archive checksums | SHA-256 of downloaded bytes matches each expected payload; Entity tags (ETags) serve only as comparison tokens. |
| Conditional creation | A second PUT with `If-None-Match: *` returns HTTP 412 and leaves the original bytes unchanged. |
| Conditional replacement | A PUT using the current ETag succeeds; a stale ETag returns HTTP 412 and preserves the replacement. |
| Multipart | The first part contains 5 mebibytes (MiB); the final part contains 17 bytes. Both remain invisible until completion. Readback matches the assembled digest. |
| Abort | An abandoned upload is aborted twice. It has no visible object. Abort after completion preserves the completed object. |
| Ambiguous PUT | The transport receives HTTP 200, discards that response, and returns an ambiguous transport error. `put_immutable` reconciles by digest without repeating the PUT. |
| Ambiguous completion | The transport discards a successful completion response. Digest reconciliation succeeds, and abort cannot delete the completed object. |
| Retention configuration | The Cloudflare bucket lock query returns `{"rules": []}`. No enforced retention is established. |
| Cleanup | Repeated cleanup succeeds. The operation prefix has no objects or unfinished uploads. |
| Source preservation | Source modes and digests match before and after the run. |

Response loss is deliberately injected after a real successful R2 response.
This verifies adapter recovery against committed provider data. It does not simulate every network failure timing.
A negative read cannot prove that an earlier uncertain write will never commit.
R2 documents atomic conditions on PUT and this limit on negative reads in its
[consistency model](https://developers.cloudflare.com/r2/reference/consistency/).
These sequential probes do not prove every concurrent execution.

The main harness reports `conditional multipart creation` as unsupported.
The separate multipart probe uses unconditional completion in this exclusively owned disposable prefix.
It does not establish immutable multipart creation or safe concurrent publication.
The adapter rejects conditional multipart operations before sending them.
Do not replace that rejection with a read followed by an unconditional write.
Use verified conditional single PUTs for publication records and immutable objects within the adapter's size limit.
Larger immutable objects require a separately verified publication mechanism; this probe does not supply one.
See the [provider capability table](storage-providers.md) and
[R2 operation compatibility](https://developers.cloudflare.com/r2/api/s3/api/).

## Retention and takeover constraints

Cloudflare documents prefix-based [bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/).
Provider support is distinct from configured enforcement. The inspected bucket has no lock rules.
The live test permits overwrite and deletion of owned data. It establishes no protection against stolen writer credentials.
Lock enforcement and historical version retrieval remain unverified through this port.
No lock is added because the resource authorization prohibits changes to bucket settings.
Do not advertise enforced retention or administrator-resistant immutability from these results.

The adapter exposes no provider fence or cross-server lease.
Conditional object writes do not establish exclusive ownership of an archive.
Keep one active archive writer and serialize mutations for each repository.
A second server opens the archive read-only.
Writable takeover requires stopping and isolating the previous writer, revoking or restricting its credentials as needed,
and reconciling committed state. A local process lock cannot fence another server.
Conflicting publication chains stop mutation; timestamps do not select a winner.
If exclusive ownership cannot be established, remain read-only. No automatic timeout-based takeover is supported.

Successful readback demonstrates observed visibility and integrity at the test time.
It cannot prove indefinite durability, freedom from provider loss, or protection against account loss.
The archive model still assumes that acknowledged durable objects remain available.
Independent checks detect corruption; an independently administered copy addresses separate account loss.
Application append-only behavior alone supplies neither provider retention nor that second copy.

## Local acceptance

`cargo xtask check` passes formatting, workspace lint checks, Rust tests, and all 179 Python acceptance tests.
The new cleanup tests cover exact deletion, repeated cleanup, changed content, foreign keys, foreign uploads, and old uploads.
The shared deterministic adapter tests exercise R2, S3, and B2 without live credentials.
They retain B2's conditional-publication rejection and R2's conditional multipart rejection.
The full check leaves tracked and unignored files unchanged.
Its log is retained beside the live evidence as `t007-check.log`.
