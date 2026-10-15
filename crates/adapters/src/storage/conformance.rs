//! Reusable conformance checks. Run only in an authorized disposable scope.
use super::{digest, verified_get};
use gitpickle_application::storage::*;
use std::collections::BTreeSet;

/// Prefixes cannot name an archive. All writes require conditional creation.
/// The caller supplies LOOPER_RESOURCE_PREFIX and a unique run identifier.
#[derive(Clone, Debug)]
pub struct TestScope {
    prefix: String,
}
impl TestScope {
    pub fn new(resource_prefix: &str, run: &str) -> Result<Self> {
        let valid = |s: &str| {
            !s.is_empty()
                && s.len() <= 128
                && s.bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
        };
        if !valid(resource_prefix) || !valid(run) {
            return Err(StorageError::InvalidInput);
        }
        Ok(Self {
            prefix: format!("{resource_prefix}/conformance/{run}/"),
        })
    }
    pub fn prefix(&self) -> &str {
        &self.prefix
    }
    fn key(&self, name: &str) -> ObjectKey {
        ObjectKey::new(format!("{}{name}", self.prefix)).unwrap()
    }
}
#[derive(Debug, Eq, PartialEq)]
pub struct Report {
    pub declared: Capabilities,
    pub checked: Vec<&'static str>,
    pub unsupported: Vec<&'static str>,
}

/// Bounded traversal accepts empty intermediate pages but rejects cursor loops,
/// duplicate keys, oversized pages, and results outside the requested prefix.
pub fn list_all(store: &mut impl Storage, prefix: &str, limit: u16) -> Result<Vec<ObjectKey>> {
    let mut cursors = BTreeSet::new();
    let mut keys = BTreeSet::new();
    let mut cursor = None;
    for _ in 0..10_000 {
        let page = store.list(prefix, cursor.as_deref(), limit)?;
        if page.keys.len() > usize::from(limit) {
            return Err(StorageError::Integrity);
        }
        for key in page.keys {
            if !key.as_str().starts_with(prefix) || !keys.insert(key) {
                return Err(StorageError::Integrity);
            }
        }
        match page.next {
            None => return Ok(keys.into_iter().collect()),
            Some(next) if cursors.insert(next.clone()) => cursor = Some(next),
            Some(_) => return Err(StorageError::InvalidCursor),
        }
    }
    Err(StorageError::InvalidCursor)
}
fn require(value: bool) -> Result<()> {
    if value {
        Ok(())
    } else {
        Err(StorageError::Integrity)
    }
}

/// No deletion, bucket configuration change, or write outside the scope occurs.
/// Refuses occupied prefixes. Leaves evidence objects for owner-checked cleanup.
/// Retention and versioning declarations are reported separately, not certified.
pub fn run(store: &mut impl Storage, scope: &TestScope) -> Result<Report> {
    let caps = store.capabilities();
    if !caps.completed_write_visibility || !caps.conditional_create {
        return Err(StorageError::Unsupported);
    }
    if !list_all(store, scope.prefix(), 1)?.is_empty() {
        return Err(StorageError::Conflict);
    }
    let mut report = Report {
        declared: caps,
        checked: vec![],
        unsupported: vec![],
    };
    let bytes = b"archive conformance payload";
    let expected = digest(bytes);
    let mut expected_keys = Vec::new();
    let mut created_revisions = Vec::new();
    for name in ["a", "b", "c"] {
        let key = scope.key(name);
        created_revisions.push(store.put(&key, bytes, expected, Condition::Absent)?);
        verified_get(store, &key, expected)?;
        expected_keys.push(key);
    }
    require(list_all(store, scope.prefix(), 1)? == expected_keys)?;
    report.checked.extend([
        "completed-write visibility",
        "pagination",
        "archive digest readback",
    ]);
    let key = scope.key("a");
    require(
        store.put(&key, b"different", digest(b"different"), Condition::Absent)
            == Err(StorageError::Conflict),
    )?;
    verified_get(store, &key, expected)?;
    report.checked.push("conditional create");
    if caps.conditional_replace {
        let original = created_revisions[0].clone();
        let replacement = b"replacement";
        let revision = store.put(
            &key,
            replacement,
            digest(replacement),
            Condition::Matches(original.clone()),
        )?;
        require(revision != original)?;
        require(
            store.put(&key, bytes, expected, Condition::Matches(original))
                == Err(StorageError::Conflict),
        )?;
        verified_get(store, &key, digest(replacement))?;
        report.checked.push("conditional replace");
    } else {
        report.unsupported.push("conditional replace");
    }
    if caps.multipart && caps.multipart_conditional_create {
        let key = scope.key("multipart");
        let multipart_bytes = vec![42; 5 * 1024 * 1024 + 17];
        let multipart_digest = digest(&multipart_bytes);
        let upload = store.begin(&key, multipart_digest, Condition::Absent)?;
        let result = (|| {
            store.part(&upload, 1, &multipart_bytes[..5 * 1024 * 1024])?;
            store.part(&upload, 2, &multipart_bytes[5 * 1024 * 1024..])?;
            require(store.get(&key) == Err(StorageError::NotFound))?;
            require(!list_all(store, scope.prefix(), 1)?.contains(&key))?;
            match store.complete(&upload, &[1, 2]) {
                Ok(_) | Err(StorageError::UnknownOutcome) => {
                    verified_get(store, &key, multipart_digest)?;
                }
                Err(error) => return Err(error),
            }
            Ok(())
        })();
        let aborted = store.abort(&upload);
        result?;
        aborted?;
        verified_get(store, &key, multipart_digest)?;
        let abandoned_key = scope.key("aborted");
        let abandoned = store.begin(&abandoned_key, expected, Condition::Absent)?;
        store.part(&abandoned, 1, bytes)?;
        store.abort(&abandoned)?;
        store.abort(&abandoned)?;
        require(store.get(&abandoned_key) == Err(StorageError::NotFound))?;
        report
            .checked
            .push("multipart visibility, completion, and abort");
    } else {
        report.unsupported.push(if caps.multipart {
            "conditional multipart creation"
        } else {
            "multipart"
        });
    }
    Ok(report)
}
