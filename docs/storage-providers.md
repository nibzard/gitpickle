# Storage providers

`storage::providers` implements the application `Storage` port with `R2`, `S3`, and `B2` adapters.
Each adapter uses the S3 HTTP API and AWS Signature Version 4.
Provider policy is explicit. An endpoint that accepts S3 requests does not prove the storage contract.
The executable accepts read-only provider configuration for `restore --from-storage`.
See [storage-only recovery](recovery.md). Other storage configuration commands remain unimplemented.

## Configuration

Construct a `Config`, runtime `Credentials`, and an adapter with a request timeout.
The configuration requires an endpoint, signing region, bucket, and nonempty prefix with a trailing slash.
Object keys include that prefix. For example, prefix `archive/` permits `archive/repos/a`.
The adapter rejects reads, writes, uploads, and listings outside its prefix.
Credentials must also restrict the scope. Client checks do not replace provider permissions.

| Adapter | Endpoint example | Signing region | Credential fields |
| --- | --- | --- | --- |
| `R2` | `https://<account-id>.r2.cloudflarestorage.com` | `auto` | R2 access key ID, secret access key, optional session token |
| `S3` | `https://s3.eu-west-1.amazonaws.com` | Actual bucket region, here `eu-west-1` | AWS access key ID, secret access key, optional session token |
| `B2` | `https://s3.us-west-004.backblazeb2.com` | Actual B2 region, here `us-west-004` | Application key ID and application key |

Endpoints contain only the origin. Requests use path-style bucket addressing.
Use the jurisdiction endpoint for a jurisdiction-restricted R2 bucket.
Select the correct regional endpoint before construction. Redirects are disabled.
HTTPS certificate validation is mandatory. Plain HTTP is limited to literal loopback addresses for tests.
Access-point aliases, directory buckets, acceleration, and custom endpoint paths are outside this implementation.

Supply secrets through a runtime credential source. Never place them in task files, arguments, or source files.
The adapter does not search credential files or refresh expiring credentials.
Replace the adapter with fresh credentials after expiration.
Adapter debug output omits credentials and transport data. Requests have no debug formatter.
Do not log request headers or archive bodies from a custom transport.

## Required operations and permissions

| Port operation | HTTP API operation | Required purpose |
| --- | --- | --- |
| `get` | `GetObject` | Read archive bytes and opaque ETag |
| `put` | `PutObject` | Write bytes with a supported condition |
| `list` | `ListObjectsV2` | Enumerate the configured prefix with continuation tokens |
| `begin` | `CreateMultipartUpload` | Initiate a private upload |
| `part` | `UploadPart` | Upload or replace a numbered part |
| `complete` | `CompleteMultipartUpload`, then `GetObject` | Commit selected parts and verify the archive digest |
| `abort` | `AbortMultipartUpload` | Remove unfinished uploads without deleting completed objects |

For AWS, grant `s3:ListBucket` on the bucket with a prefix restriction.
Grant `s3:GetObject`, `s3:PutObject`, and `s3:AbortMultipartUpload` on objects under the prefix.
Multipart initiation, parts, and completion use `s3:PutObject`.
No bucket creation, bucket policy, version deletion, or object deletion permission is required.
Encrypted AWS buckets can require separate key permissions. Configure those through the bucket policy and key policy.

For R2, use a bucket-scoped **Object Read & Write** token.
Where available, use temporary credentials with the seven API operations above and the permitted paths.
The adapter never changes bucket settings.

For B2, use a non-master application key restricted to the bucket and file prefix.
The operations require `listFiles`, `readFiles`, and `writeFiles`.
`writeFiles` also permits multipart abort. The adapter does not require `deleteFiles` or bucket discovery.
B2 documents that `writeFiles` can also delete a file by name through other clients.
Application append-only behavior therefore does not establish protection against stolen writer credentials.
Grant no retention override or administrative permissions to a routine writer.

## Declared capabilities

These declarations express the adapter's permitted protocol. They are not live verification evidence.

| Capability | R2 | AWS S3 | B2 through its S3 API |
| --- | --- | --- | --- |
| Completed-write visibility | Declared | Declared | Declared |
| Conditional PUT creation and replacement | Enabled | Enabled | Unavailable in this adapter |
| Multipart uploads without conditions | Enabled | Enabled | Enabled |
| Conditional multipart creation and replacement | Unavailable in this adapter | Enabled | Unavailable in this adapter |
| Historical version retrieval through this port | Unavailable | Unavailable | Unavailable |
| Provider retention | Available, enforcement unverified | Available, enforcement unverified | Available, enforcement unverified |

R2 documents atomic conditional PUT operations.
Its compatibility table does not explicitly document conditions on multipart completion.
The adapter rejects conditional multipart initiation before sending a request.
Use conditional single PUTs for immutable objects within the single PUT size limit.
It never substitutes a read followed by an unconditional write.

B2's PUT and completion documentation does not establish atomic conditional writes.
The adapter rejects `Absent` and `Matches` for those operations.
B2 therefore cannot currently pass the immutable publication prerequisite in the shared harness.
Read, inventory, unconditional write, and unconditional multipart operations are implemented and tested locally.
Do not enable B2 as an immutable archive writer until the missing guarantee is established.
Provider verification must record this limitation, or justify and test a different publication mechanism.

`versioning = false` means this port cannot inspect or retrieve historical versions.
It does not assert that AWS or B2 lacks bucket versioning.
`retention = Available` never means retention is configured or enforced.
No adapter configures retention, retrieves historical versions, deletes objects, or fences another writer.
Takeover still requires the archive's explicit fencing and reconciliation procedure.

## Integrity, multipart staging, and retries

PUT validates the supplied archive SHA-256 digest before sending bytes.
Signature Version 4 signs the payload digest, condition, and session token.
ETags remain opaque comparison tokens. Use `verified_get` for independent content verification.
The implementation does not treat provider checksum metadata as archive evidence.

Multipart initiation retains the condition for completion.
Successful parts are staged in private temporary files and removed when the upload state is dropped.
File names use `LOOPER_RESOURCE_PREFIX` when set. The operating system temporary directory stores them.
The default permissions protect staged bytes. Secure that directory and provide sufficient disk space.
The adapter hashes the selected staged parts before completion, then hashes the completed object's readback.
Parts use numbers 1 through 10,000. Nonfinal parts require at least 5 MiB.
Each part and single PUT have a 5 GiB limit. Multipart objects have a 5 TiB limit.
R2 also requires equal nonfinal part sizes and a final part no larger than the others.
A failed part retry invalidates the cached ETag; upload that part again before completion.
This conservative rule handles R2's documented replacement behavior.

The synchronous port buffers each request and each object read in memory.
Multipart staging avoids retaining every uploaded part in memory.
The port does not resume multipart state after process loss.
A failed initiation can leave an upload whose identifier was lost.
Provider lifecycle cleanup for abandoned uploads requires separate authorization.

Each adapter operation sends one request, except completion, which also reads the completed object.
Transport retries are disabled. There are no hidden write retries or redirects.
A connection failure before sending returns `Unavailable`.
A failed read returns `Unavailable`, or `Throttled` for an explicit throttle response.
Authentication failures return `Unauthorized`. Failed write conditions return `Conflict`.
Unsupported operations return `Unsupported`.
Timeouts, uncertain mutation responses, missing write ETags, and server errors preserve `UnknownOutcome`.
Completion also checks for an error document inside an HTTP 200 response.
A readback mismatch returns `Integrity`, even after successful provider completion.

Use `put_immutable` for bounded conditional creation with digest reconciliation.
It reads after a conflict or unknown outcome before another attempt.
After uncertain multipart completion, reconcile the key and digest before starting another upload.
Abort sends only an upload-specific DELETE request. It cannot remove a completed object.
An already completed or aborted local handle makes abort a no-op.
Caller scheduling controls delay, jitter, cancellation, and the overall retry budget.
No retry turns an unknown outcome into proof that the object is absent.

## Verification

Run local checks without credentials:

```sh
cargo test --locked --package gitpickle-adapters
cargo xtask check
```

Tests use fixed signing time, a scripted transport, the shared contract emulator, and a free loopback port.
They check the AWS signing example, encoded keys, tokens, conditions, pagination, lost responses, and multipart errors.
They also check corrupt readback, upload ownership, abort safety, and provider capability rejection.
The shared harness passes S3's declared operations and reports R2's missing conditional multipart capability.
It rejects B2's missing conditional creation before mutation.
Separate tests cover unconditional multipart operations for R2 and B2.

No live provider tests run as part of this suite.
T007 owns live R2 evidence. T053 supplies final S3/B2 access, and T054 owns those live checks.
Use the isolation and cleanup procedure in [the storage contract](storage-contract.md) before any live mutation.
Save generated reports under ignored `tests/results/storage/` or `tests/results/storage-wire/`.
Local test success does not satisfy those provider verification gates.

## Protocol references

Provider policy was reviewed on 15 October 2026. Recheck it during live verification.

- [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- [AWS multipart completion and embedded errors](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html)
- [AWS permissions for API operations](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html)
- [AWS Signature Version 4 example](https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html)
- [R2 S3 compatibility and operation-specific limits](https://developers.cloudflare.com/r2/api/s3/api/)
- [R2 temporary credential operations and paths](https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)
- [B2 PUT operation](https://www.backblaze.com/apidocs/s3-put-object)
- [B2 multipart completion](https://www.backblaze.com/apidocs/s3-complete-multipart-upload)
- [B2 S3 API restrictions](https://www.backblaze.com/docs/cloud-storage-s3-compatible-api)
- [B2 S3 application key capabilities](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys)
