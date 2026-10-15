//! Explicit live runner. Invoke through tools/gitpickle_r2_conformance.py.
use gitpickle_adapters::storage::{
    conformance::{TestScope, probe_unconditional_multipart, run},
    digest,
    providers::{
        Config, Credentials, HttpTransport, R2, Request, Response, Transport, TransportError,
    },
    put_immutable, verified_get,
};
use gitpickle_application::storage::*;
use reqwest::Method;
use serde_json::{Value, json};
use std::{cell::Cell, env, fs::OpenOptions, io::Write, rc::Rc, time::Duration};

struct Audited {
    http: HttpTransport,
    journal: std::fs::File,
    prefix: String,
    lose: Rc<Cell<u8>>,
    parts: std::collections::BTreeMap<String, std::collections::BTreeMap<u16, Vec<u8>>>,
}
impl Audited {
    fn record(&mut self, value: Value) -> std::io::Result<()> {
        writeln!(self.journal, "{value}")?;
        self.journal.sync_all()
    }
}
impl Transport for Audited {
    fn send(&mut self, request: Request) -> std::result::Result<Response, TransportError> {
        let key = request
            .url
            .path()
            .splitn(3, '/')
            .nth(2)
            .unwrap_or("")
            .to_owned();
        let query: std::collections::BTreeMap<_, _> =
            request.url.query_pairs().into_owned().collect();
        let mutation = request.method != Method::GET;
        let initiate = request.method == Method::POST && query.contains_key("uploads");
        let complete = request.method == Method::POST && query.contains_key("uploadId");
        let put = request.method == Method::PUT && !query.contains_key("uploadId");
        if mutation && !key.starts_with(&self.prefix) {
            return Err(TransportError::BeforeSend);
        }
        if request.method == Method::PUT
            && let Some(number) = query.get("partNumber")
        {
            self.parts
                .entry(key.clone())
                .or_default()
                .insert(number.parse().unwrap(), request.body.clone());
        }
        let object_sha256 = if complete {
            self.parts.get(&key).map(|parts| {
                let bytes: Vec<u8> = parts.values().flatten().copied().collect();
                digest(&bytes)
                    .0
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>()
            })
        } else {
            None
        };
        let hash: String = digest(&request.body)
            .0
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        // Flush intent before the request. Never log signatures, headers, or bodies.
        self.record(json!({"event":"intent", "method":request.method.as_str(),
            "key":key, "query":query, "sha256":hash, "object_sha256":object_sha256, "bytes":request.body.len()}))
            .map_err(|_| TransportError::BeforeSend)?;
        let method = request.method.clone();
        let response = self.http.send(request)?;
        let mut upload_id = None;
        if initiate && response.status == 200 {
            #[derive(serde::Deserialize)]
            struct Initiated {
                #[serde(rename = "UploadId")]
                id: String,
            }
            upload_id = quick_xml::de::from_reader::<_, Initiated>(response.body.as_slice())
                .ok()
                .map(|v| v.id);
        }
        let dropped = response.status == 200
            && ((self.lose.get() == 1 && put) || (self.lose.get() == 2 && complete));
        self.record(
            json!({"event":"response", "method":method.as_str(), "key":key,
            "status":response.status, "upload_id":upload_id, "dropped":dropped}),
        )
        .map_err(|_| TransportError::Ambiguous)?;
        if dropped {
            self.lose.set(0);
            Err(TransportError::Ambiguous)
        } else {
            Ok(response)
        }
    }
}
fn now() -> String {
    chrono::Utc::now().format("%Y%m%dT%H%M%SZ").to_string()
}
fn execute() -> std::result::Result<(), Box<dyn std::error::Error>> {
    let state: Value = serde_json::from_slice(&std::fs::read(env::var("GITPICKLE_R2_RECEIPT")?)?)?;
    let config: Value = serde_json::from_str(&env::var("GITPICKLE_STORAGE_CONFIG")?)?;
    let text = |v: &Value, k: &str| v[k].as_str().unwrap().to_owned();
    let scope = TestScope::within(
        &text(&config, "prefix"),
        &text(&state, "resource_prefix"),
        &text(&state, "run"),
    )
    .map_err(|e| format!("scope: {e:?}"))?;
    assert_eq!(scope.prefix(), text(&state, "object_prefix"));
    let lose = Rc::new(Cell::new(0));
    let transport = Audited {
        http: HttpTransport::new(Duration::from_secs(45))
            .map_err(|e| format!("transport: {e:?}"))?,
        journal: OpenOptions::new()
            .append(true)
            .open(text(&state, "journal"))?,
        prefix: scope.prefix().into(),
        lose: lose.clone(),
        parts: Default::default(),
    };
    let mut store = R2::with_transport(
        Config::new(
            &text(&config, "endpoint"),
            &text(&config, "region"),
            &text(&config, "bucket"),
            scope.prefix(),
        )
        .map_err(|e| format!("config: {e:?}"))?,
        Credentials::new(
            env::var("GITPICKLE_STORAGE_ACCESS_KEY")?,
            env::var("GITPICKLE_STORAGE_SECRET_KEY")?,
            None,
        )
        .map_err(|e| format!("credentials: {e:?}"))?,
        transport,
        now,
    )
    .map_err(|e| format!("adapter: {e:?}"))?;
    let result = (|| -> Result<Value> {
        let report = run(&mut store, &scope)?;
        probe_unconditional_multipart(&mut store, &scope)?;
        let key = ObjectKey::new(format!("{}ambiguous-put", scope.prefix()))?;
        lose.set(1);
        put_immutable(&mut store, &key, b"ambiguous publication", 2)?;
        if lose.get() != 0 {
            return Err(StorageError::Integrity);
        }
        verified_get(&mut store, &key, digest(b"ambiguous publication"))?;
        // A nested scope keeps both multipart probes inside the owned prefix.
        let nested = TestScope::within(scope.prefix(), &text(&state, "resource_prefix"), "lost")?;
        lose.set(2);
        probe_unconditional_multipart(&mut store, &nested)?;
        if lose.get() != 0 {
            return Err(StorageError::Integrity);
        }
        Ok(
            json!({"schema_version":1, "provider":"r2", "checked":report.checked,
            "unsupported":report.unsupported, "capabilities":format!("{:?}",report.declared),
            "supplemental":["unconditional multipart visibility, completion, digest readback, abort",
                "successful PUT response discarded; immutable reconciliation",
                "successful multipart completion response discarded; digest reconciliation"],
            "retention_enforcement":"unverified", "s3_b2_verified":false}),
        )
    })();
    let report = match result {
        Ok(value) => value,
        Err(error) => json!({"schema_version":1,"provider":"r2","failure":format!("{error:?}")}),
    };
    std::fs::write(text(&state, "result"), serde_json::to_vec_pretty(&report)?)?;
    if report.get("failure").is_some() {
        return Err("R2 conformance failed; see nonsecret report".into());
    }
    Ok(())
}
fn main() {
    if let Err(error) = execute() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
