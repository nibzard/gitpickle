//! Content-addressed publication for an externally fenced single archive writer.
//! This is not an implementation of conditional writes or a distributed lock.
use super::digest;
use gitpickle_application::storage::*;
use std::collections::BTreeSet;

pub const NAMESPACE: &str = ".gitpickle-publication/v1/";

pub fn hex_digest(bytes: &[u8]) -> String {
    digest(bytes).0.iter().map(|b| format!("{b:02x}")).collect()
}

/// Decode a physical name. Unknown versions and malformed digests fail closed.
pub fn decode(relative: &str) -> Result<(&str, Option<&str>)> {
    if !relative.starts_with(".gitpickle-publication/") {
        return Ok((relative, None));
    }
    let encoded = relative
        .strip_prefix(NAMESPACE)
        .ok_or(StorageError::Unsupported)?;
    let (logical, hash) = encoded.rsplit_once('/').ok_or(StorageError::Integrity)?;
    ObjectKey::new(logical)?;
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(StorageError::Integrity);
    }
    Ok((logical, Some(hash)))
}

/// Names include the complete content digest: different bytes never target the
/// same physical object. Retries may create provider versions of identical bytes.
/// One active writer and external fencing before takeover remain prerequisites.
pub fn publish(store: &mut dyn Storage, prefix: &str, logical: &str, bytes: &[u8]) -> Result<()> {
    if !store.capabilities().completed_write_visibility {
        return Err(StorageError::Unsupported);
    }
    ObjectKey::new(prefix.strip_suffix('/').ok_or(StorageError::InvalidInput)?)?;
    ObjectKey::new(logical)?;
    if logical.starts_with(".gitpickle-publication/") {
        return Err(StorageError::InvalidInput);
    }
    let scope = format!("{prefix}{NAMESPACE}{logical}/");
    let key = ObjectKey::new(format!("{scope}{}", hex_digest(bytes)))?;
    // Preserve existing archives; never replace legacy logical objects.
    let legacy = ObjectKey::new(format!("{prefix}{logical}"))?;
    match store.get(&legacy) {
        Ok(object) if object.bytes != bytes => return Err(StorageError::Conflict),
        Ok(_) | Err(StorageError::NotFound) => {}
        Err(error) => return Err(error),
    }
    let mut cursor = None;
    let mut seen = BTreeSet::new();
    loop {
        let page = store.list(&scope, cursor.as_deref(), 1000)?;
        if page.keys.iter().any(|found| found != &key) {
            return Err(StorageError::Conflict);
        }
        match page.next {
            None => break,
            Some(next) => {
                if !seen.insert(next.clone()) {
                    return Err(StorageError::InvalidCursor);
                }
                cursor = Some(next);
            }
        }
    }
    match store.get(&key) {
        Ok(object) => {
            return if object.bytes == bytes {
                Ok(())
            } else {
                Err(StorageError::Integrity)
            };
        }
        Err(StorageError::NotFound) => {}
        Err(error) => return Err(error),
    }
    match store.put(&key, bytes, digest(bytes), Condition::Any) {
        Ok(_) | Err(StorageError::UnknownOutcome) => {}
        Err(error) => return Err(error),
    }
    // Only remote readback can resolve a lost response. No local-cache fallback.
    if store.get(&key)?.bytes != bytes {
        return Err(StorageError::Integrity);
    }
    Ok(())
}
