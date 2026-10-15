//! Provider-specific policies over a signed S3 wire protocol.
//! See docs/storage-providers.md for permissions and unverified capabilities.
use super::digest;
use gitpickle_application::storage::*;
use hmac::{Hmac, Mac};
use reqwest::{Method, Url, blocking::Client, redirect::Policy};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fmt,
    io::{Read, Seek, SeekFrom, Write},
    time::Duration,
};
use tempfile::NamedTempFile;

/// Deliberately has no Debug implementation. Callers supply secrets at runtime.
pub struct Credentials {
    access_key: String,
    secret_key: String,
    session_token: Option<String>,
}
impl Credentials {
    pub fn new(
        access_key: String,
        secret_key: String,
        session_token: Option<String>,
    ) -> Result<Self> {
        if access_key.is_empty()
            || secret_key.is_empty()
            || access_key.chars().any(|c| !c.is_ascii_alphanumeric())
            || session_token
                .as_ref()
                .is_some_and(|s| s.is_empty() || !valid_header(s))
        {
            return Err(StorageError::InvalidInput);
        }
        Ok(Self {
            access_key,
            secret_key,
            session_token,
        })
    }
}

#[derive(Clone, Debug)]
pub struct Config {
    endpoint: Url,
    region: String,
    bucket: String,
    prefix: String,
}
impl Config {
    /// Endpoint is an HTTPS origin, without credentials, path, query, or fragment.
    /// HTTP is permitted only for literal loopback addresses in local tests.
    /// Prefix is a nonempty directory, including the trailing slash.
    pub fn new(endpoint: &str, region: &str, bucket: &str, prefix: &str) -> Result<Self> {
        let endpoint = Url::parse(endpoint).map_err(|_| StorageError::InvalidInput)?;
        let loopback = endpoint.host_str().is_some_and(|s| {
            s.parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
        });
        if !(endpoint.scheme() == "https" || endpoint.scheme() == "http" && loopback)
            || endpoint.host_str().is_none()
            || !endpoint.username().is_empty()
            || endpoint.password().is_some()
            || endpoint.path() != "/"
            || endpoint.query().is_some()
            || endpoint.fragment().is_some()
            || region.is_empty()
            || !region
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'-')
            || !(3..=63).contains(&bucket.len())
            || !bucket
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-' || c == b'.')
            || !bucket.as_bytes()[0].is_ascii_alphanumeric()
            || !bucket.as_bytes()[bucket.len() - 1].is_ascii_alphanumeric()
            || !prefix.ends_with('/')
        {
            return Err(StorageError::InvalidInput);
        }
        ObjectKey::new(prefix.trim_end_matches('/'))?;
        if prefix.ends_with("//") {
            return Err(StorageError::InvalidInput);
        }
        Ok(Self {
            endpoint,
            region: region.into(),
            bucket: bucket.into(),
            prefix: prefix.into(),
        })
    }
    fn key(&self, key: &ObjectKey) -> Result<()> {
        if key.as_str().starts_with(&self.prefix) {
            Ok(())
        } else {
            Err(StorageError::InvalidInput)
        }
    }
    fn cursor_marker(&self, prefix: &str) -> String {
        // Bind tokens to provider location and exact prefix, without secrets.
        format!(
            "{}:",
            hex(&digest(
                format!(
                    "{}\n{}\n{}\n{prefix}",
                    self.endpoint, self.region, self.bucket
                )
                .as_bytes()
            )
            .0)
        )
    }
}

/// Headers and bodies can contain credentials or archive data. Never log them.
pub struct Request {
    pub method: Method,
    pub url: Url,
    pub headers: BTreeMap<String, String>,
    pub body: Vec<u8>,
}
pub struct Response {
    pub status: u16,
    /// Header names must be lowercase.
    pub headers: BTreeMap<String, String>,
    pub body: Vec<u8>,
}
#[derive(Clone, Copy, Debug)]
pub enum TransportError {
    /// No request bytes reached the service.
    BeforeSend,
    /// The service might have received some or all of the request.
    Ambiguous,
}
pub trait Transport {
    fn send(&mut self, request: Request) -> std::result::Result<Response, TransportError>;
}

pub struct HttpTransport(Client);
impl HttpTransport {
    pub fn new(timeout: Duration) -> Result<Self> {
        if timeout.is_zero() {
            return Err(StorageError::InvalidInput);
        }
        Client::builder()
            .redirect(Policy::none())
            .retry(reqwest::retry::never())
            .connect_timeout(timeout.min(Duration::from_secs(10)))
            .timeout(timeout)
            .build()
            .map(Self)
            .map_err(|_| StorageError::Unavailable)
    }
}
impl Transport for HttpTransport {
    fn send(&mut self, request: Request) -> std::result::Result<Response, TransportError> {
        let mut builder = self.0.request(request.method, request.url);
        for (name, value) in request.headers {
            builder = builder.header(name, value);
        }
        let response = builder.body(request.body).send().map_err(|e| {
            if e.is_connect() || e.is_builder() {
                TransportError::BeforeSend
            } else {
                TransportError::Ambiguous
            }
        })?;
        let status = response.status().as_u16();
        let headers = response
            .headers()
            .iter()
            .filter_map(|(name, value)| {
                value
                    .to_str()
                    .ok()
                    .map(|value| (name.to_string(), value.to_owned()))
            })
            .collect();
        let body = response
            .bytes()
            .map_err(|_| TransportError::Ambiguous)?
            .to_vec();
        Ok(Response {
            status,
            headers,
            body,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Kind {
    S3,
    R2,
    B2,
}
fn capabilities(kind: Kind) -> Capabilities {
    Capabilities {
        completed_write_visibility: true,
        conditional_create: kind != Kind::B2,
        conditional_replace: kind != Kind::B2,
        multipart: true,
        multipart_conditional_create: kind == Kind::S3,
        multipart_conditional_replace: kind == Kind::S3,
        // This port cannot inspect or retrieve historical versions.
        versioning: false,
        // Available does not claim enforcement on this bucket or prefix.
        retention: RetentionCapability::Available,
    }
}
struct Part {
    etag: String,
    file: NamedTempFile,
    size: u64,
}
struct Pending {
    key: ObjectKey,
    expected: ArchiveDigest,
    condition: Condition,
    parts: BTreeMap<u16, Part>,
}
struct Provider<T> {
    kind: Kind,
    config: Config,
    credentials: Credentials,
    transport: T,
    clock: fn() -> String,
    uploads: BTreeMap<String, Pending>,
}
fn now() -> String {
    chrono::Utc::now().format("%Y%m%dT%H%M%SZ").to_string()
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn encode(value: &str, slash: bool) -> String {
    let mut result = String::new();
    for b in value.bytes() {
        if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) || slash && b == b'/' {
            result.push(char::from(b));
        } else {
            result.push_str(&format!("%{b:02X}"));
        }
    }
    result
}
fn valid_header(s: &str) -> bool {
    s.bytes().all(|b| (32..=126).contains(&b))
}
fn hmac(key: &[u8], bytes: &[u8]) -> Vec<u8> {
    let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("HMAC accepts any key length");
    mac.update(bytes);
    mac.finalize().into_bytes().to_vec()
}
fn sign(request: &mut Request, credentials: &Credentials, region: &str, date: &str) -> Result<()> {
    if !date.is_ascii()
        || date.len() != 16
        || !date[..8].bytes().all(|b| b.is_ascii_digit())
        || date.as_bytes()[8] != b'T'
        || date.as_bytes()[15] != b'Z'
        || !date[9..15].bytes().all(|b| b.is_ascii_digit())
    {
        return Err(StorageError::InvalidInput);
    }
    let host = request.url.host_str().ok_or(StorageError::InvalidInput)?;
    let host = match request.url.port() {
        Some(port) => format!("{host}:{port}"),
        None => host.to_owned(),
    };
    request.headers.insert("host".into(), host);
    request.headers.insert("x-amz-date".into(), date.into());
    request
        .headers
        .insert("x-amz-content-sha256".into(), hex(&digest(&request.body).0));
    if let Some(token) = &credentials.session_token {
        request
            .headers
            .insert("x-amz-security-token".into(), token.clone());
    }
    let names = request
        .headers
        .keys()
        .cloned()
        .collect::<Vec<_>>()
        .join(";");
    let headers = request
        .headers
        .iter()
        .map(|(k, v)| {
            format!(
                "{k}:{}\n",
                v.split_ascii_whitespace().collect::<Vec<_>>().join(" ")
            )
        })
        .collect::<String>();
    let canonical = format!(
        "{}\n{}\n{}\n{headers}\n{names}\n{}",
        request.method,
        request.url.path(),
        request.url.query().unwrap_or(""),
        hex(&digest(&request.body).0)
    );
    let scope = format!("{}/{region}/s3/aws4_request", &date[..8]);
    let to_sign = format!(
        "AWS4-HMAC-SHA256\n{date}\n{scope}\n{}",
        hex(&digest(canonical.as_bytes()).0)
    );
    let key = hmac(
        format!("AWS4{}", credentials.secret_key).as_bytes(),
        &date.as_bytes()[..8],
    );
    let key = hmac(
        &hmac(&hmac(&key, region.as_bytes()), b"s3"),
        b"aws4_request",
    );
    request.headers.insert(
        "authorization".into(),
        format!(
            "AWS4-HMAC-SHA256 Credential={}/{scope}, SignedHeaders={names}, Signature={}",
            credentials.access_key,
            hex(&hmac(&key, to_sign.as_bytes()))
        ),
    );
    Ok(())
}
fn xml<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    quick_xml::de::from_reader(bytes).map_err(|_| StorageError::Integrity)
}
#[derive(Deserialize)]
struct ErrorBody {
    #[serde(rename = "Code")]
    code: String,
}
fn service_error(response: &Response, mutation: bool) -> StorageError {
    let code = xml::<ErrorBody>(&response.body)
        .map(|e| e.code)
        .unwrap_or_default();
    match (response.status, code.as_str()) {
        (401 | 403, _)
        | (
            _,
            "AccessDenied"
            | "InvalidAccessKeyId"
            | "SignatureDoesNotMatch"
            | "ExpiredToken"
            | "InvalidToken",
        ) => StorageError::Unauthorized,
        (412 | 409, _) | (_, "PreconditionFailed" | "ConditionalRequestConflict") => {
            StorageError::Conflict
        }
        (404, _) | (_, "NoSuchKey" | "NoSuchUpload") => StorageError::NotFound,
        (429, _) | (_, "SlowDown" | "Throttling") => {
            if mutation {
                StorageError::UnknownOutcome
            } else {
                StorageError::Throttled
            }
        }
        (501 | 405, _) | (_, "NotImplemented") => StorageError::Unsupported,
        (_, "InvalidPart" | "InvalidPartOrder" | "EntityTooSmall" | "InvalidArgument") => {
            StorageError::InvalidInput
        }
        _ if mutation => StorageError::UnknownOutcome,
        _ => StorageError::Unavailable,
    }
}
fn etag(headers: &BTreeMap<String, String>) -> Result<Revision> {
    let value = headers
        .get("etag")
        .filter(|s| !s.is_empty() && valid_header(s))
        .ok_or(StorageError::Integrity)?;
    Ok(Revision(value.clone()))
}
fn conditional_error(error: StorageError, condition: &Condition) -> StorageError {
    if error == StorageError::NotFound && matches!(condition, Condition::Matches(_)) {
        StorageError::Conflict
    } else {
        error
    }
}
fn condition_headers(condition: &Condition) -> Result<BTreeMap<String, String>> {
    let mut headers = BTreeMap::new();
    match condition {
        Condition::Any => {}
        Condition::Absent => {
            headers.insert("if-none-match".into(), "*".into());
        }
        Condition::Matches(Revision(value)) => {
            // A single strong entity tag, never a wildcard or a list of tags.
            if value.len() < 2
                || !value.starts_with('"')
                || !value.ends_with('"')
                || !valid_header(value)
                || value[1..value.len() - 1].contains('"')
            {
                return Err(StorageError::InvalidInput);
            }
            headers.insert("if-match".into(), value.clone());
        }
    }
    Ok(headers)
}
#[derive(Deserialize)]
struct Initiated {
    #[serde(rename = "UploadId")]
    id: String,
}
#[derive(Deserialize)]
struct Completed {
    #[serde(rename = "ETag")]
    etag: String,
}
#[derive(Deserialize)]
struct Listed {
    #[serde(rename = "Contents", default)]
    contents: Vec<ListedKey>,
    #[serde(rename = "IsTruncated")]
    truncated: bool,
    #[serde(rename = "NextContinuationToken")]
    next: Option<String>,
}
#[derive(Deserialize)]
struct ListedKey {
    #[serde(rename = "Key")]
    key: String,
}
impl<T: Transport> Provider<T> {
    fn request(
        &mut self,
        method: Method,
        key: Option<&ObjectKey>,
        query: Vec<(&str, String)>,
        headers: BTreeMap<String, String>,
        body: Vec<u8>,
    ) -> Result<Response> {
        let mutation = method != Method::GET;
        let path = format!(
            "/{}/{}",
            self.config.bucket,
            key.map(|k| encode(k.as_str(), true)).unwrap_or_default()
        );
        let mut query: Vec<_> = query
            .into_iter()
            .map(|(k, v)| (encode(k, false), encode(&v, false)))
            .collect();
        query.sort();
        let query = query
            .into_iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect::<Vec<_>>()
            .join("&");
        let mut url = self.config.endpoint.clone();
        url.set_path(&path);
        if !query.is_empty() {
            url.set_query(Some(&query));
        }
        let mut request = Request {
            method,
            url,
            headers,
            body,
        };
        sign(
            &mut request,
            &self.credentials,
            &self.config.region,
            &(self.clock)(),
        )?;
        let response = self.transport.send(request).map_err(|e| match e {
            TransportError::BeforeSend => StorageError::Unavailable,
            TransportError::Ambiguous if mutation => StorageError::UnknownOutcome,
            TransportError::Ambiguous => StorageError::Unavailable,
        })?;
        if !(200..300).contains(&response.status) {
            return Err(service_error(&response, mutation));
        }
        Ok(response)
    }
    fn check_condition(&self, condition: &Condition, multipart: bool) -> Result<()> {
        let caps = capabilities(self.kind);
        let (create, replace) = if multipart {
            (
                caps.multipart_conditional_create,
                caps.multipart_conditional_replace,
            )
        } else {
            (caps.conditional_create, caps.conditional_replace)
        };
        match condition {
            Condition::Absent if !create => Err(StorageError::Unsupported),
            Condition::Matches(_) if !replace => Err(StorageError::Unsupported),
            _ => {
                condition_headers(condition)?;
                Ok(())
            }
        }
    }
    fn pending(&mut self, upload: &Upload) -> Result<&mut Pending> {
        self.config.key(&upload.key)?;
        self.uploads
            .get_mut(&upload.id)
            .filter(|p| p.key == upload.key)
            .ok_or(StorageError::NotFound)
    }
}
impl<T: Transport> Storage for Provider<T> {
    fn capabilities(&self) -> Capabilities {
        capabilities(self.kind)
    }
    fn get(&mut self, key: &ObjectKey) -> Result<Object> {
        self.config.key(key)?;
        let response = self.request(Method::GET, Some(key), vec![], BTreeMap::new(), vec![])?;
        Ok(Object {
            revision: etag(&response.headers)?,
            bytes: response.body,
        })
    }
    fn put(
        &mut self,
        key: &ObjectKey,
        bytes: &[u8],
        expected: ArchiveDigest,
        condition: Condition,
    ) -> Result<Revision> {
        self.config.key(key)?;
        self.check_condition(&condition, false)?;
        if bytes.len() as u64 > 5 * 1024 * 1024 * 1024 {
            return Err(StorageError::InvalidInput);
        }
        if digest(bytes) != expected {
            return Err(StorageError::Integrity);
        }
        let response = self
            .request(
                Method::PUT,
                Some(key),
                vec![],
                condition_headers(&condition)?,
                bytes.to_vec(),
            )
            .map_err(|error| conditional_error(error, &condition))?;
        // A missing revision after a successful mutation is still ambiguous.
        etag(&response.headers).map_err(|_| StorageError::UnknownOutcome)
    }
    fn list(&mut self, prefix: &str, cursor: Option<&str>, limit: u16) -> Result<ObjectPage> {
        if !prefix.starts_with(&self.config.prefix) || !(1..=1000).contains(&limit) {
            return Err(StorageError::InvalidInput);
        }
        let marker = self.config.cursor_marker(prefix);
        let mut query = vec![
            ("list-type", "2".into()),
            ("prefix", prefix.into()),
            ("max-keys", limit.to_string()),
        ];
        if let Some(cursor) = cursor {
            let token = cursor
                .strip_prefix(&marker)
                .filter(|s| !s.is_empty())
                .ok_or(StorageError::InvalidCursor)?;
            query.push(("continuation-token", token.into()));
        }
        let response = self.request(Method::GET, None, query, BTreeMap::new(), vec![])?;
        let listed: Listed = xml(&response.body)?;
        let keys: Vec<_> = listed
            .contents
            .into_iter()
            .map(|entry| ObjectKey::new(entry.key))
            .collect::<Result<_>>()?;
        if keys.len() > usize::from(limit)
            || keys.iter().any(|k| !k.as_str().starts_with(prefix))
            || keys.windows(2).any(|w| w[0] >= w[1])
        {
            return Err(StorageError::Integrity);
        }
        let next = if listed.truncated {
            Some(format!(
                "{marker}{}",
                listed
                    .next
                    .filter(|s| !s.is_empty())
                    .ok_or(StorageError::InvalidCursor)?
            ))
        } else {
            None
        };
        if next.as_deref().is_some_and(|n| Some(n) == cursor) {
            return Err(StorageError::InvalidCursor);
        }
        Ok(ObjectPage { keys, next })
    }
    fn begin(
        &mut self,
        key: &ObjectKey,
        expected: ArchiveDigest,
        condition: Condition,
    ) -> Result<Upload> {
        self.config.key(key)?;
        self.check_condition(&condition, true)?;
        let response = self.request(
            Method::POST,
            Some(key),
            vec![("uploads", "".into())],
            BTreeMap::new(),
            vec![],
        )?;
        let initiated: Initiated = xml(&response.body).map_err(|_| StorageError::UnknownOutcome)?;
        if initiated.id.is_empty() || self.uploads.contains_key(&initiated.id) {
            return Err(StorageError::UnknownOutcome);
        }
        let id = initiated.id;
        self.uploads.insert(
            id.clone(),
            Pending {
                key: key.clone(),
                expected,
                condition,
                parts: BTreeMap::new(),
            },
        );
        Ok(Upload {
            key: key.clone(),
            id,
        })
    }
    fn part(&mut self, upload: &Upload, number: u16, bytes: &[u8]) -> Result<()> {
        self.pending(upload)?;
        if !(1..=10000).contains(&number) || bytes.len() as u64 > 5 * 1024 * 1024 * 1024 {
            return Err(StorageError::InvalidInput);
        }
        // A failed retry can invalidate the old part on R2. Do not retain its token.
        self.pending(upload)?.parts.remove(&number);
        let prefix = std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "gitpickle".into());
        let prefix: String = prefix
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '-')
            .take(80)
            .collect();
        let mut file = tempfile::Builder::new()
            .prefix(&format!("{prefix}-storage-part-"))
            .tempfile()
            .map_err(|_| StorageError::Unavailable)?;
        file.write_all(bytes)
            .map_err(|_| StorageError::Unavailable)?;
        let response = self.request(
            Method::PUT,
            Some(&upload.key),
            vec![
                ("partNumber", number.to_string()),
                ("uploadId", upload.id.clone()),
            ],
            BTreeMap::new(),
            bytes.to_vec(),
        )?;
        let etag = etag(&response.headers)
            .map_err(|_| StorageError::UnknownOutcome)?
            .0;
        self.pending(upload)?.parts.insert(
            number,
            Part {
                etag,
                file,
                size: bytes.len() as u64,
            },
        );
        Ok(())
    }
    fn complete(&mut self, upload: &Upload, parts: &[u16]) -> Result<Revision> {
        if parts.is_empty() || parts[0] == 0 || parts.windows(2).any(|w| w[0] >= w[1]) {
            return Err(StorageError::InvalidInput);
        }
        let kind = self.kind;
        let pending = self.pending(upload)?;
        let mut hasher = Sha256::new();
        let mut body = String::from("<CompleteMultipartUpload>");
        let mut buffer = [0; 64 * 1024];
        let mut first_size = None;
        let mut total_size = 0;
        for (index, number) in parts.iter().enumerate() {
            let part = pending
                .parts
                .get_mut(number)
                .ok_or(StorageError::InvalidInput)?;
            total_size += part.size;
            if total_size > 5 * 1024 * 1024 * 1024 * 1024
                || kind == Kind::R2 && first_size.is_some_and(|s| part.size > s)
            {
                return Err(StorageError::InvalidInput);
            }
            if index + 1 < parts.len() {
                if part.size < 5 * 1024 * 1024
                    || kind == Kind::R2 && first_size.is_some_and(|s| s != part.size)
                {
                    return Err(StorageError::InvalidInput);
                }
                first_size = Some(part.size);
            }
            part.file
                .seek(SeekFrom::Start(0))
                .map_err(|_| StorageError::Unavailable)?;
            loop {
                let count = part
                    .file
                    .read(&mut buffer)
                    .map_err(|_| StorageError::Unavailable)?;
                if count == 0 {
                    break;
                }
                hasher.update(&buffer[..count]);
            }
            body.push_str(&format!(
                "<Part><PartNumber>{number}</PartNumber><ETag>{}</ETag></Part>",
                quick_xml::escape::escape(&part.etag)
            ));
        }
        body.push_str("</CompleteMultipartUpload>");
        if ArchiveDigest(hasher.finalize().into()) != pending.expected {
            return Err(StorageError::Integrity);
        }
        let expected = pending.expected;
        let condition = pending.condition.clone();
        let headers = condition_headers(&pending.condition)?;
        let response = self
            .request(
                Method::POST,
                Some(&upload.key),
                vec![("uploadId", upload.id.clone())],
                headers,
                body.into_bytes(),
            )
            .map_err(|error| conditional_error(error, &condition))?;
        // S3 can return HTTP 200 with an Error document after starting completion.
        if xml::<ErrorBody>(&response.body).is_ok() {
            return Err(service_error(&response, true));
        }
        let completed: Completed = xml(&response.body).map_err(|_| StorageError::UnknownOutcome)?;
        if completed.etag.is_empty() {
            return Err(StorageError::UnknownOutcome);
        }
        self.uploads.remove(&upload.id);
        // Verify actual bytes independently of composite ETags and metadata.
        let object = self
            .get(&upload.key)
            .map_err(|_| StorageError::UnknownOutcome)?;
        if digest(&object.bytes) != expected {
            return Err(StorageError::Integrity);
        }
        Ok(object.revision)
    }
    fn abort(&mut self, upload: &Upload) -> Result<()> {
        self.config.key(&upload.key)?;
        // Unknown handles cannot authorize a DELETE request. Completed handles are no-ops.
        if !self.uploads.contains_key(&upload.id) {
            return Ok(());
        }
        self.pending(upload)?;
        match self.request(
            Method::DELETE,
            Some(&upload.key),
            vec![("uploadId", upload.id.clone())],
            BTreeMap::new(),
            vec![],
        ) {
            Ok(_) | Err(StorageError::NotFound) => {
                self.uploads.remove(&upload.id);
                Ok(())
            }
            Err(e) => Err(e),
        }
    }
}
macro_rules! adapter {
    ($name:ident, $kind:ident) => {
        pub struct $name<T = HttpTransport>(Provider<T>);
        impl $name<HttpTransport> {
            pub fn new(
                config: Config,
                credentials: Credentials,
                timeout: Duration,
            ) -> Result<Self> {
                Self::with_transport(config, credentials, HttpTransport::new(timeout)?, now)
            }
        }
        impl<T: Transport> $name<T> {
            /// Inject a transport and UTC signing clock for deterministic tests.
            pub fn with_transport(
                config: Config,
                credentials: Credentials,
                transport: T,
                clock: fn() -> String,
            ) -> Result<Self> {
                if Kind::$kind == Kind::R2 && config.region != "auto" {
                    return Err(StorageError::InvalidInput);
                }
                Ok(Self(Provider {
                    kind: Kind::$kind,
                    config,
                    credentials,
                    transport,
                    clock,
                    uploads: BTreeMap::new(),
                }))
            }
        }
        impl<T: Transport> Storage for $name<T> {
            fn capabilities(&self) -> Capabilities {
                self.0.capabilities()
            }
            fn get(&mut self, key: &ObjectKey) -> Result<Object> {
                self.0.get(key)
            }
            fn put(
                &mut self,
                key: &ObjectKey,
                bytes: &[u8],
                digest: ArchiveDigest,
                condition: Condition,
            ) -> Result<Revision> {
                self.0.put(key, bytes, digest, condition)
            }
            fn list(
                &mut self,
                prefix: &str,
                cursor: Option<&str>,
                limit: u16,
            ) -> Result<ObjectPage> {
                self.0.list(prefix, cursor, limit)
            }
            fn begin(
                &mut self,
                key: &ObjectKey,
                digest: ArchiveDigest,
                condition: Condition,
            ) -> Result<Upload> {
                self.0.begin(key, digest, condition)
            }
            fn part(&mut self, upload: &Upload, number: u16, bytes: &[u8]) -> Result<()> {
                self.0.part(upload, number, bytes)
            }
            fn complete(&mut self, upload: &Upload, parts: &[u16]) -> Result<Revision> {
                self.0.complete(upload, parts)
            }
            fn abort(&mut self, upload: &Upload) -> Result<()> {
                self.0.abort(upload)
            }
        }
        impl<T> fmt::Debug for $name<T> {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.debug_struct(stringify!($name)).finish_non_exhaustive()
            }
        }
    };
}
adapter!(S3, S3);
adapter!(R2, R2);
adapter!(B2, B2);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signature_matches_the_aws_s3_get_object_example() {
        // AWS's public example key and signature, not runtime credentials.
        // https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
        let credentials = Credentials::new(
            "AKIAIOSFODNN7EXAMPLE".into(),
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY".into(),
            None,
        )
        .unwrap();
        let mut request = Request {
            method: Method::GET,
            url: Url::parse("https://examplebucket.s3.amazonaws.com/test.txt").unwrap(),
            headers: BTreeMap::from([("range".into(), "bytes=0-9".into())]),
            body: vec![],
        };
        sign(&mut request, &credentials, "us-east-1", "20130524T000000Z").unwrap();
        assert_eq!(
            request.headers["authorization"],
            "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
        );
    }
}
