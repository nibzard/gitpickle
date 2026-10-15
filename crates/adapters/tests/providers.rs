//! Deterministic wire tests. No provider credentials or external resources.
use gitpickle_adapters::storage::{
    conformance::*, digest, providers::*, put_immutable, verified_get,
};
use gitpickle_application::storage::*;
use reqwest::Method;
use std::{
    collections::{BTreeMap, VecDeque},
    sync::{Arc, Mutex},
};

fn clock() -> String {
    "20261015T080000Z".into()
}
fn credentials() -> Credentials {
    Credentials::new(
        "TESTACCESS".into(),
        "test-secret".into(),
        Some("test-session".into()),
    )
    .unwrap()
}
fn config(region: &str) -> Config {
    Config::new("https://storage.example", region, "test-bucket", "private/").unwrap()
}
fn key() -> ObjectKey {
    ObjectKey::new("private/data").unwrap()
}
fn response(status: u16, body: impl AsRef<[u8]>) -> Response {
    Response {
        status,
        headers: BTreeMap::new(),
        body: body.as_ref().to_vec(),
    }
}
fn object(body: &[u8], revision: &str) -> Response {
    let mut response = response(200, body);
    response.headers.insert("etag".into(), revision.into());
    response
}
#[derive(Default)]
struct Script {
    responses: VecDeque<std::result::Result<Response, TransportError>>,
    requests: Vec<Request>,
}
#[derive(Clone, Default)]
struct Mock(Arc<Mutex<Script>>);
impl Mock {
    fn replies(replies: Vec<std::result::Result<Response, TransportError>>) -> Self {
        Self(Arc::new(Mutex::new(Script {
            responses: replies.into(),
            requests: vec![],
        })))
    }
    fn inspect(&self, check: impl FnOnce(&[Request])) {
        check(&self.0.lock().unwrap().requests);
    }
}
impl Transport for Mock {
    fn send(&mut self, request: Request) -> std::result::Result<Response, TransportError> {
        let mut state = self.0.lock().unwrap();
        state.requests.push(request);
        state.responses.pop_front().expect("unexpected request")
    }
}

#[test]
fn signed_put_preserves_bytes_conditions_tokens_and_escaped_keys() {
    let mock = Mock::replies(vec![
        Ok(object(b"", "\"opaque\"")),
        Ok(object(b"", "\"next\"")),
    ]);
    let mut store =
        S3::with_transport(config("eu-west-1"), credentials(), mock.clone(), clock).unwrap();
    let key = ObjectKey::new("private/a b+%?雪/#").unwrap();
    let revision = store
        .put(&key, b"payload", digest(b"payload"), Condition::Absent)
        .unwrap();
    store
        .put(&key, b"next", digest(b"next"), Condition::Matches(revision))
        .unwrap();
    mock.inspect(|requests| {
        assert_eq!(
            requests[0].url.as_str(),
            "https://storage.example/test-bucket/private/a%20b%2B%25%3F%E9%9B%AA/%23"
        );
        assert_eq!(requests[0].body, b"payload");
        assert_eq!(requests[0].headers["if-none-match"], "*");
        assert_eq!(requests[0].headers["x-amz-security-token"], "test-session");
        assert!(
            requests[0].headers["authorization"].contains("20261015/eu-west-1/s3/aws4_request")
        );
        assert!(requests[0].headers["authorization"].contains("if-none-match"));
        assert_eq!(requests[1].headers["if-match"], "\"opaque\"");
        assert_ne!(
            requests[0].headers["authorization"],
            requests[1].headers["authorization"]
        );
    });
    assert!(!format!("{store:?}").contains("test-secret"));
}

#[test]
fn lost_put_is_reconciled_without_repeating_the_write() {
    let mock = Mock::replies(vec![
        Err(TransportError::Ambiguous),
        Ok(object(b"payload", "\"etag\"")),
    ]);
    let mut store = R2::with_transport(config("auto"), credentials(), mock.clone(), clock).unwrap();
    put_immutable(&mut store, &key(), b"payload", 3).unwrap();
    mock.inspect(|r| {
        assert_eq!(
            r.iter().map(|r| r.method.clone()).collect::<Vec<_>>(),
            vec![Method::PUT, Method::GET]
        )
    });
}

#[test]
fn rejected_conditions_and_digests_never_send_requests() {
    let mock = Mock::default();
    let mut store =
        S3::with_transport(config("eu-west-1"), credentials(), mock.clone(), clock).unwrap();
    assert_eq!(
        store.put(&key(), b"wrong", digest(b"right"), Condition::Absent),
        Err(StorageError::Integrity)
    );
    for tag in ["*", "", "W/\"a\"", "\"a\", \"b\"", "\"a\r\nb\""] {
        assert_eq!(
            store.put(
                &key(),
                b"x",
                digest(b"x"),
                Condition::Matches(Revision(tag.into()))
            ),
            Err(StorageError::InvalidInput)
        );
    }
    assert_eq!(
        store.get(&ObjectKey::new("elsewhere/data").unwrap()),
        Err(StorageError::InvalidInput)
    );
    assert_eq!(store.list("", None, 1), Err(StorageError::InvalidInput));
    mock.inspect(|r| assert!(r.is_empty()));
}

#[test]
fn provider_capabilities_are_independent_and_unsupported_calls_are_local() {
    let mock = Mock::default();
    let mut b2 =
        B2::with_transport(config("us-west-004"), credentials(), mock.clone(), clock).unwrap();
    let mut r2 = R2::with_transport(config("auto"), credentials(), mock.clone(), clock).unwrap();
    let s3 = S3::with_transport(config("eu-west-1"), credentials(), mock.clone(), clock).unwrap();
    assert!(s3.capabilities().multipart_conditional_create);
    assert!(r2.capabilities().conditional_create);
    assert!(!r2.capabilities().multipart_conditional_create);
    assert!(!b2.capabilities().conditional_create);
    for condition in [
        Condition::Absent,
        Condition::Matches(Revision("\"r\"".into())),
    ] {
        assert_eq!(
            b2.put(&key(), b"x", digest(b"x"), condition.clone()),
            Err(StorageError::Unsupported)
        );
        assert_eq!(
            r2.begin(&key(), digest(b"x"), condition.clone()),
            Err(StorageError::Unsupported)
        );
        assert_eq!(
            b2.begin(&key(), digest(b"x"), condition),
            Err(StorageError::Unsupported)
        );
    }
    for caps in [s3.capabilities(), r2.capabilities(), b2.capabilities()] {
        assert_ne!(caps.retention, RetentionCapability::Enforced);
        assert!(!caps.versioning);
    }
    mock.inspect(|r| assert!(r.is_empty()));
}

#[test]
fn errors_distinguish_permissions_conflicts_absence_and_unknown_mutations() {
    for (status, code, expected) in [
        (403, "AccessDenied", StorageError::Unauthorized),
        (412, "PreconditionFailed", StorageError::Conflict),
        (409, "ConditionalRequestConflict", StorageError::Conflict),
        (404, "NoSuchKey", StorageError::NotFound),
        (501, "NotImplemented", StorageError::Unsupported),
        (503, "InternalError", StorageError::UnknownOutcome),
        (429, "SlowDown", StorageError::UnknownOutcome),
    ] {
        let mock = Mock::replies(vec![Ok(response(
            status,
            format!("<Error><Code>{code}</Code></Error>"),
        ))]);
        let mut store =
            S3::with_transport(config("eu-west-1"), credentials(), mock.clone(), clock).unwrap();
        assert_eq!(
            store.put(&key(), b"x", digest(b"x"), Condition::Absent),
            Err(expected)
        );
        mock.inspect(|r| assert_eq!(r.len(), 1));
    }
    for (error, expected) in [
        (TransportError::BeforeSend, StorageError::Unavailable),
        (TransportError::Ambiguous, StorageError::UnknownOutcome),
    ] {
        let mut store = S3::with_transport(
            config("us-east-1"),
            credentials(),
            Mock::replies(vec![Err(error)]),
            clock,
        )
        .unwrap();
        assert_eq!(
            store.put(&key(), b"x", digest(b"x"), Condition::Any),
            Err(expected)
        );
    }
    let mut store = S3::with_transport(
        config("us-east-1"),
        credentials(),
        Mock::replies(vec![Ok(response(
            503,
            "<Error><Code>SlowDown</Code></Error>",
        ))]),
        clock,
    )
    .unwrap();
    assert_eq!(store.get(&key()), Err(StorageError::Throttled));
}

fn page(keys: &[&str], next: Option<&str>) -> Response {
    response(
        200,
        format!(
            "<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><IsTruncated>{}</IsTruncated>{}{}</ListBucketResult>",
            next.is_some(),
            keys.iter()
                .map(|k| format!(
                    "<Contents><Key>{}</Key></Contents>",
                    quick_xml::escape::escape(*k)
                ))
                .collect::<String>(),
            next.map(|n| format!(
                "<NextContinuationToken>{}</NextContinuationToken>",
                quick_xml::escape::escape(n)
            ))
            .unwrap_or_default()
        ),
    )
}
#[test]
fn pagination_preserves_provider_tokens_empty_pages_and_prefix_binding() {
    let mock = Mock::replies(vec![
        Ok(page(&[], Some("opaque+/=&"))),
        Ok(page(&["private/a"], None)),
    ]);
    let mut store =
        B2::with_transport(config("us-west-004"), credentials(), mock.clone(), clock).unwrap();
    let cursor = store.list("private/", None, 1).unwrap().next.unwrap();
    assert_eq!(
        store.list("private/sub/", Some(&cursor), 1),
        Err(StorageError::InvalidCursor)
    );
    assert_eq!(
        store.list("private/", Some(&cursor), 1).unwrap().keys,
        vec![ObjectKey::new("private/a").unwrap()]
    );
    mock.inspect(|r| {
        assert!(
            r[1].url
                .as_str()
                .contains("continuation-token=opaque%2B%2F%3D%26")
        )
    });
}
#[test]
fn malformed_or_unsafe_listing_is_rejected() {
    for reply in [
        page(&["other/a"], None),
        page(&["private/a", "private/a"], None),
        response(
            200,
            "<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>",
        ),
        response(200, "garbage"),
    ] {
        let mut store = R2::with_transport(
            config("auto"),
            credentials(),
            Mock::replies(vec![Ok(reply)]),
            clock,
        )
        .unwrap();
        assert!(store.list("private/", None, 2).is_err());
    }
}

fn initiated() -> Response {
    response(
        200,
        "<InitiateMultipartUploadResult><UploadId>upload+/&amp;</UploadId></InitiateMultipartUploadResult>",
    )
}
fn completed() -> Response {
    response(
        200,
        "<CompleteMultipartUploadResult><ETag>\"composite-1\"</ETag></CompleteMultipartUploadResult>",
    )
}
#[test]
fn multipart_checks_digest_order_and_visibility_then_reads_back_completion() {
    let first = vec![42; 5 * 1024 * 1024];
    let mut bytes = first.clone();
    bytes.extend(b"tail");
    let mock = Mock::replies(vec![
        Ok(initiated()),
        Ok(object(b"", "\"part1\"")),
        Ok(object(b"", "\"part2\"")),
        Ok(completed()),
        Ok(object(&bytes, "\"composite-2\"")),
    ]);
    let mut store =
        S3::with_transport(config("us-east-1"), credentials(), mock.clone(), clock).unwrap();
    let upload = store
        .begin(&key(), digest(&bytes), Condition::Absent)
        .unwrap();
    store.part(&upload, 1, &first).unwrap();
    store.part(&upload, 2, b"tail").unwrap();
    assert_eq!(
        store.complete(&upload, &[2, 1]),
        Err(StorageError::InvalidInput)
    );
    assert_eq!(
        store.complete(&upload, &[1, 3]),
        Err(StorageError::InvalidInput)
    );
    assert_eq!(store.complete(&upload, &[1]), Err(StorageError::Integrity));
    assert_eq!(
        store.complete(&upload, &[1, 2]).unwrap(),
        Revision("\"composite-2\"".into())
    );
    store.abort(&upload).unwrap();
    store.abort(&upload).unwrap();
    mock.inspect(|r| {
        assert_eq!(r.len(), 5);
        assert!(!r[0].headers.contains_key("if-none-match"));
        assert_eq!(r[3].headers["if-none-match"], "*");
        assert!(r[3].url.as_str().contains("uploadId=upload%2B%2F%26"));
        assert!(String::from_utf8_lossy(&r[3].body).contains("&quot;part1&quot;"));
        assert_eq!(r[4].method, Method::GET);
    });
}

#[test]
fn multipart_error_inside_http_200_is_not_success() {
    for code in ["InternalError", "SlowDown", "PreconditionFailed"] {
        let mock = Mock::replies(vec![
            Ok(initiated()),
            Ok(object(b"", "\"part\"")),
            Ok(response(
                200,
                format!("  <Error><Code>{code}</Code></Error>"),
            )),
            Ok(response(204, "")),
        ]);
        let mut store =
            S3::with_transport(config("us-east-1"), credentials(), mock.clone(), clock).unwrap();
        let upload = store
            .begin(&key(), digest(b"x"), Condition::Absent)
            .unwrap();
        store.part(&upload, 1, b"x").unwrap();
        assert_eq!(
            store.complete(&upload, &[1]),
            Err(if code == "PreconditionFailed" {
                StorageError::Conflict
            } else {
                StorageError::UnknownOutcome
            })
        );
        store.abort(&upload).unwrap();
        mock.inspect(|r| assert_eq!(r.len(), 4));
    }
}
#[test]
fn lost_completion_requires_reconciliation_and_abort_cannot_delete_objects() {
    let mock = Mock::replies(vec![
        Ok(initiated()),
        Ok(object(b"", "\"part\"")),
        Err(TransportError::Ambiguous),
        Ok(object(b"x", "\"composite\"")),
        Ok(response(404, "<Error><Code>NoSuchUpload</Code></Error>")),
    ]);
    let mut store =
        S3::with_transport(config("us-east-1"), credentials(), mock.clone(), clock).unwrap();
    let upload = store
        .begin(&key(), digest(b"x"), Condition::Absent)
        .unwrap();
    store.part(&upload, 1, b"x").unwrap();
    assert_eq!(
        store.complete(&upload, &[1]),
        Err(StorageError::UnknownOutcome)
    );
    verified_get(&mut store, &key(), digest(b"x")).unwrap();
    store.abort(&upload).unwrap();
    store.abort(&upload).unwrap();
    mock.inspect(|r| {
        assert_eq!(r.len(), 5);
        assert_eq!(r[4].method, Method::DELETE);
        assert!(r[4].url.query().unwrap().starts_with("uploadId="));
    });
}
#[test]
fn failed_part_retry_invalidates_old_token_and_foreign_uploads_are_rejected() {
    let mock = Mock::replies(vec![
        Ok(initiated()),
        Ok(object(b"", "\"part\"")),
        Err(TransportError::Ambiguous),
        Ok(response(204, "")),
    ]);
    let mut store = R2::with_transport(config("auto"), credentials(), mock.clone(), clock).unwrap();
    let upload = store.begin(&key(), digest(b"x"), Condition::Any).unwrap();
    store.part(&upload, 1, b"x").unwrap();
    assert_eq!(
        store.part(&upload, 1, b"x"),
        Err(StorageError::UnknownOutcome)
    );
    assert_eq!(
        store.complete(&upload, &[1]),
        Err(StorageError::InvalidInput)
    );
    let mut foreign = upload.clone();
    foreign.key = ObjectKey::new("private/other").unwrap();
    assert_eq!(store.part(&foreign, 1, b"x"), Err(StorageError::NotFound));
    assert_eq!(store.abort(&foreign), Err(StorageError::NotFound));
    store.abort(&upload).unwrap();
}
#[test]
fn completion_corruption_is_detected_even_with_a_successful_provider_response() {
    let mock = Mock::replies(vec![
        Ok(initiated()),
        Ok(object(b"", "\"part\"")),
        Ok(completed()),
        Ok(object(b"bad", "\"etag\"")),
    ]);
    let mut store = B2::with_transport(config("us-west-004"), credentials(), mock, clock).unwrap();
    let upload = store.begin(&key(), digest(b"x"), Condition::Any).unwrap();
    store.part(&upload, 1, b"x").unwrap();
    assert_eq!(store.complete(&upload, &[1]), Err(StorageError::Integrity));
}
#[test]
fn config_rejects_unsafe_endpoints_and_invalid_scope() {
    for endpoint in [
        "http://storage.example",
        "https://user:secret@storage.example",
        "https://storage.example/path",
        "https://storage.example/?q=1",
        "https://storage.example/#fragment",
        "file:///tmp/",
    ] {
        assert!(Config::new(endpoint, "auto", "bucket", "private/").is_err());
    }
    for prefix in [
        "",
        "/",
        "private",
        "private//",
        "../private/",
        "private/../a/",
    ] {
        assert!(Config::new("https://storage.example", "auto", "bucket", prefix).is_err());
    }
    assert!(
        R2::with_transport(config("us-east-1"), credentials(), Mock::default(), clock).is_err()
    );
    assert!(Credentials::new("key".into(), "secret".into(), Some("bad\nheader".into())).is_err());
}

/// Translate the wire protocol into the existing contract emulator. This checks
/// adapter serialization through the same harness used for live conformance.
#[derive(Default)]
struct Wire {
    store: gitpickle_adapters::storage::emulator::Emulator,
    uploads: BTreeMap<String, (ObjectKey, BTreeMap<u16, Vec<u8>>)>,
    sequence: usize,
}
impl Wire {
    fn handle(&mut self, r: Request) -> Result<Response> {
        let path = r.url.path().strip_prefix("/test-bucket/").unwrap();
        let query: BTreeMap<_, _> = r.url.query_pairs().into_owned().collect();
        let condition = if r.headers.contains_key("if-none-match") {
            Condition::Absent
        } else if let Some(tag) = r.headers.get("if-match") {
            Condition::Matches(Revision(tag.trim_matches('"').into()))
        } else {
            Condition::Any
        };
        if query.contains_key("list-type") {
            let listed = self.store.list(
                &query["prefix"],
                query.get("continuation-token").map(String::as_str),
                query["max-keys"].parse().unwrap(),
            )?;
            return Ok(page(
                &listed
                    .keys
                    .iter()
                    .map(ObjectKey::as_str)
                    .collect::<Vec<_>>(),
                listed.next.as_deref(),
            ));
        }
        let key = ObjectKey::new(path)?;
        if query.contains_key("uploads") {
            self.sequence += 1;
            let id = format!("upload-{}", self.sequence);
            self.uploads.insert(id.clone(), (key, BTreeMap::new()));
            return Ok(response(
                200,
                format!(
                    "<InitiateMultipartUploadResult><UploadId>{id}</UploadId></InitiateMultipartUploadResult>"
                ),
            ));
        }
        if let Some(id) = query.get("uploadId") {
            if r.method == Method::DELETE {
                self.uploads.remove(id);
                return Ok(response(204, ""));
            }
            let (_, parts) = self.uploads.get_mut(id).ok_or(StorageError::NotFound)?;
            if r.method == Method::PUT {
                parts.insert(query["partNumber"].parse().unwrap(), r.body);
                return Ok(object(b"", "\"part\""));
            }
            let bytes: Vec<_> = parts.values().flatten().copied().collect();
            self.store.put(&key, &bytes, digest(&bytes), condition)?;
            self.uploads.remove(id);
            return Ok(completed());
        }
        if r.method == Method::GET {
            let got = self.store.get(&key)?;
            Ok(object(&got.bytes, &format!("\"{}\"", got.revision.0)))
        } else {
            let revision = self.store.put(&key, &r.body, digest(&r.body), condition)?;
            Ok(object(b"", &format!("\"{}\"", revision.0)))
        }
    }
}
impl Transport for Wire {
    fn send(&mut self, r: Request) -> std::result::Result<Response, TransportError> {
        Ok(match self.handle(r) {
            Ok(response) => response,
            Err(StorageError::Conflict) => {
                response(412, "<Error><Code>PreconditionFailed</Code></Error>")
            }
            Err(StorageError::NotFound) => response(404, "<Error><Code>NoSuchKey</Code></Error>"),
            Err(error) => panic!("unexpected emulator error: {error:?}"),
        })
    }
}
#[test]
fn adapters_use_the_shared_conformance_harness_without_claiming_missing_capabilities() {
    let prefix = std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "local-test".into());
    let scope = TestScope::new(&prefix, "provider-wire").unwrap();
    let config = |region| {
        Config::new(
            "https://storage.example",
            region,
            "test-bucket",
            &format!("{prefix}/"),
        )
        .unwrap()
    };
    let mut s3 =
        S3::with_transport(config("us-east-1"), credentials(), Wire::default(), clock).unwrap();
    assert!(run(&mut s3, &scope).unwrap().unsupported.is_empty());
    let mut r2 = R2::with_transport(config("auto"), credentials(), Wire::default(), clock).unwrap();
    probe_unconditional_multipart(&mut r2, &scope).unwrap();
    // Use a separate emulator instance for the occupied-prefix guard.
    let mut r2 = R2::with_transport(config("auto"), credentials(), Wire::default(), clock).unwrap();
    assert_eq!(
        run(&mut r2, &scope).unwrap().unsupported,
        vec!["conditional multipart creation"]
    );
    let mut b2 =
        B2::with_transport(config("us-west-004"), credentials(), Wire::default(), clock).unwrap();
    assert_eq!(run(&mut b2, &scope), Err(StorageError::Unsupported));
    probe_unconditional_multipart(&mut b2, &scope).unwrap();
}

#[test]
fn http_transport_sends_signed_bytes_and_does_not_follow_redirects() {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        time::Duration,
    };
    // The listener owns a free local port. Dropping it closes the resource.
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let worker = std::thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut bytes = Vec::new();
        let mut buffer = [0; 4096];
        loop {
            let count = socket.read(&mut buffer).unwrap();
            assert_ne!(count, 0);
            bytes.extend_from_slice(&buffer[..count]);
            if let Some(end) = bytes.windows(4).position(|w| w == b"\r\n\r\n")
                && bytes.len() >= end + 4 + 7
            {
                break;
            }
        }
        let request = String::from_utf8(bytes).unwrap();
        assert!(request.starts_with("PUT /test-bucket/private/data HTTP/1.1"));
        assert!(request.contains("authorization: AWS4-HMAC-SHA256"));
        assert!(request.contains("if-none-match: *"));
        assert!(request.ends_with("payload"));
        socket.write_all(b"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://must-not-follow.invalid/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
    });
    let mut store = S3::new(
        Config::new(&endpoint, "us-east-1", "test-bucket", "private/").unwrap(),
        credentials(),
        Duration::from_secs(5),
    )
    .unwrap();
    assert_eq!(
        store.put(&key(), b"payload", digest(b"payload"), Condition::Absent),
        Err(StorageError::UnknownOutcome)
    );
    worker.join().unwrap();
}

#[test]
fn missing_replace_target_is_a_failed_condition() {
    let mock = Mock::replies(vec![Ok(response(
        404,
        "<Error><Code>NoSuchKey</Code></Error>",
    ))]);
    let mut store = S3::with_transport(config("us-east-1"), credentials(), mock, clock).unwrap();
    assert_eq!(
        store.put(
            &key(),
            b"x",
            digest(b"x"),
            Condition::Matches(Revision("\"old\"".into()))
        ),
        Err(StorageError::Conflict)
    );
}

#[test]
fn incomplete_success_responses_keep_mutations_ambiguous() {
    let mock = Mock::replies(vec![
        Ok(response(200, "")),
        Ok(response(200, "invalid xml")),
    ]);
    let mut store = S3::with_transport(config("us-east-1"), credentials(), mock, clock).unwrap();
    assert_eq!(
        store.put(&key(), b"x", digest(b"x"), Condition::Absent),
        Err(StorageError::UnknownOutcome)
    );
    assert_eq!(
        store.begin(&key(), digest(b"x"), Condition::Absent),
        Err(StorageError::UnknownOutcome)
    );
}

#[test]
fn multipart_replacement_checks_the_condition_at_completion() {
    let mock = Mock::replies(vec![
        Ok(initiated()),
        Ok(object(b"", "\"part\"")),
        Ok(response(
            412,
            "<Error><Code>PreconditionFailed</Code></Error>",
        )),
        Ok(response(204, "")),
    ]);
    let mut store =
        S3::with_transport(config("us-east-1"), credentials(), mock.clone(), clock).unwrap();
    let upload = store
        .begin(
            &key(),
            digest(b"x"),
            Condition::Matches(Revision("\"old\"".into())),
        )
        .unwrap();
    store.part(&upload, 1, b"x").unwrap();
    assert_eq!(store.complete(&upload, &[1]), Err(StorageError::Conflict));
    store.abort(&upload).unwrap();
    mock.inspect(|r| {
        assert!(!r[0].headers.contains_key("if-match"));
        assert_eq!(r[2].headers["if-match"], "\"old\"");
    });
}

#[test]
fn r2_and_b2_support_unconditional_multipart_without_claiming_conditional_safety() {
    fn exercise(store: &mut impl Storage) {
        let upload = store.begin(&key(), digest(b"x"), Condition::Any).unwrap();
        store.part(&upload, 1, b"x").unwrap();
        assert!(store.complete(&upload, &[1]).is_ok());
        store.abort(&upload).unwrap();
    }
    let mock = || {
        Mock::replies(vec![
            Ok(initiated()),
            Ok(object(b"", "\"part\"")),
            Ok(completed()),
            Ok(object(b"x", "\"etag\"")),
        ])
    };
    exercise(&mut R2::with_transport(config("auto"), credentials(), mock(), clock).unwrap());
    exercise(&mut B2::with_transport(config("us-west-004"), credentials(), mock(), clock).unwrap());
}

#[test]
fn b2_archive_publication_uses_unconditional_digest_names_and_remote_readback() {
    use gitpickle_adapters::storage::publication::{NAMESPACE, hex_digest, publish};
    let mock = Mock::replies(vec![
        Ok(response(404, "<Error><Code>NoSuchKey</Code></Error>")),
        Ok(page(&[], None)),
        Ok(response(404, "<Error><Code>NoSuchKey</Code></Error>")),
        Err(TransportError::Ambiguous),
        Ok(object(b"identity", "\"opaque\"")),
    ]);
    let mut store =
        B2::with_transport(config("us-west-004"), credentials(), mock.clone(), clock).unwrap();
    publish(&mut store, "private/", "archive.json", b"identity").unwrap();
    assert!(!store.capabilities().conditional_create);
    mock.inspect(|requests| {
        assert_eq!(requests.len(), 5);
        assert_eq!(requests[3].method, Method::PUT);
        assert!(!requests[3].headers.contains_key("if-none-match"));
        assert!(!requests[3].headers.contains_key("if-match"));
        assert_eq!(
            requests[3].url.path(),
            format!(
                "/test-bucket/private/{NAMESPACE}archive.json/{}",
                hex_digest(b"identity")
            )
        );
        assert_eq!(requests[3].url, requests[4].url);
        assert_eq!(requests[4].method, Method::GET);
    });
}
