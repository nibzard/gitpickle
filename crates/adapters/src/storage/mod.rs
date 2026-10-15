//! Storage adapters, deterministic emulation, and provider conformance tools.
pub mod conformance;
pub mod emulator;
pub mod providers;
use gitpickle_application::storage::*;
use sha2::{Digest, Sha256};

pub fn digest(bytes: &[u8]) -> ArchiveDigest {
    ArchiveDigest(Sha256::digest(bytes).into())
}
pub fn verified_get(
    store: &mut impl Storage,
    key: &ObjectKey,
    expected: ArchiveDigest,
) -> Result<Object> {
    let object = store.get(key)?;
    if digest(&object.bytes) != expected {
        return Err(StorageError::Integrity);
    }
    Ok(object)
}

/// Bounded immutable upload. Unknown outcomes require readback before retry.
/// An existing different object is a conflict, even if its provider token agrees.
pub fn put_immutable(
    store: &mut impl Storage,
    key: &ObjectKey,
    bytes: &[u8],
    attempts: u16,
) -> Result<Revision> {
    if attempts == 0 {
        return Err(StorageError::InvalidInput);
    }
    if !store.capabilities().conditional_create {
        return Err(StorageError::Unsupported);
    }
    let expected = digest(bytes);
    for _ in 0..attempts {
        match store.put(key, bytes, expected, Condition::Absent) {
            Ok(revision) => return Ok(revision),
            Err(StorageError::Conflict | StorageError::UnknownOutcome) => {
                match verified_get(store, key, expected) {
                    Ok(object) => return Ok(object.revision),
                    Err(StorageError::NotFound | StorageError::Unavailable) => continue,
                    Err(error) => return Err(error),
                }
            }
            Err(StorageError::Unavailable) => continue,
            Err(error) => return Err(error),
        }
    }
    Err(StorageError::UnknownOutcome)
}
