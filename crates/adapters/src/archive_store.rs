//! Private subprocess boundary for the embedded archive writer and storage ports.
use crate::{
    recovery::download_archive,
    storage::{digest, providers::*},
};
use gitpickle_application::storage::*;
use serde::Deserialize;
use std::{ffi::OsString, fs, path::Path, time::Duration};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Location {
    provider: String,
    endpoint: String,
    region: String,
    bucket: String,
    prefix: String,
}

/// Upload immutable bytes, including reconciliation after an ambiguous response.
fn persist(store: &mut dyn Storage, key: &ObjectKey, bytes: &[u8]) -> Result<()> {
    match store.get(key) {
        Ok(object) => {
            return if object.bytes == bytes {
                Ok(())
            } else {
                Err(StorageError::Conflict)
            };
        }
        Err(StorageError::NotFound) => {}
        Err(error) => return Err(error),
    }
    if !store.capabilities().conditional_create {
        return Err(StorageError::Unsupported);
    }
    // Archive packs are bounded by the capture adapter. Larger payloads require
    // a separate streaming implementation; never fall back to an unsafe write.
    match store.put(key, bytes, digest(bytes), Condition::Absent) {
        Ok(_) | Err(StorageError::UnknownOutcome | StorageError::Conflict) => {}
        Err(error) => return Err(error),
    }
    if store.get(key)?.bytes != bytes {
        return Err(StorageError::Integrity);
    }
    Ok(())
}

pub fn run(args: Vec<OsString>) -> std::result::Result<u8, String> {
    fn execute(args: Vec<OsString>) -> std::result::Result<(), String> {
        let location: Location = serde_json::from_str(
            &std::env::var("GITPICKLE_STORAGE_CONFIG").map_err(|_| "configuration")?,
        )
        .map_err(|_| "configuration")?;
        let config = Config::new(
            &location.endpoint,
            &location.region,
            &location.bucket,
            &location.prefix,
        )
        .map_err(|_| "configuration")?;
        let credentials = Credentials::new(
            std::env::var("GITPICKLE_STORAGE_ACCESS_KEY").map_err(|_| "authentication")?,
            std::env::var("GITPICKLE_STORAGE_SECRET_KEY").map_err(|_| "authentication")?,
            std::env::var("GITPICKLE_STORAGE_SESSION_TOKEN").ok(),
        )
        .map_err(|_| "authentication")?;
        let timeout = Duration::from_secs(60);
        let mut store: Box<dyn Storage> = match location.provider.as_str() {
            "r2" => Box::new(R2::new(config, credentials, timeout).map_err(|_| "configuration")?),
            "s3" => Box::new(S3::new(config, credentials, timeout).map_err(|_| "configuration")?),
            "b2" => Box::new(B2::new(config, credentials, timeout).map_err(|_| "configuration")?),
            _ => return Err("configuration".into()),
        };
        match args.first().and_then(|s| s.to_str()) {
            Some("capabilities") if args.len() == 1 => {
                let caps = store.capabilities();
                println!(
                    "{}",
                    serde_json::json!({
                        "conditional_create": caps.conditional_create,
                        "completed_write_visibility": caps.completed_write_visibility,
                        "retention_enforced": false
                    })
                );
            }
            Some("download") if args.len() == 2 => {
                download_archive(store.as_mut(), &location.prefix, Path::new(&args[1]))?;
            }
            Some("put") if args.len() == 3 => {
                let relative = args[1].to_str().ok_or("invalid_input")?;
                ObjectKey::new(relative).map_err(|_| "invalid_input")?;
                let key = ObjectKey::new(format!("{}{relative}", location.prefix))
                    .map_err(|_| "invalid_input")?;
                let bytes = fs::read(&args[2]).map_err(|_| "storage_unavailable")?;
                persist(store.as_mut(), &key, &bytes).map_err(|e| format!("{e:?}"))?;
            }
            _ => return Err("invalid_input".into()),
        }
        Ok(())
    }
    match execute(args) {
        Ok(()) => Ok(0),
        Err(_) => {
            // Never include provider bodies, request URLs, or credential material.
            eprintln!(
                "storage_unavailable: Check storage access and required conditional-write capabilities."
            );
            Ok(5)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::emulator::{Emulator, Fault, Operation};
    #[test]
    fn immutable_upload_reconciles_retries_and_checks_readback() {
        let mut store = Emulator::default();
        let key = ObjectKey::new("owned/record").unwrap();
        store.inject(Operation::Put, Fault::LostResponse);
        persist(&mut store, &key, b"archive").unwrap();
        persist(&mut store, &key, b"archive").unwrap();
        assert_eq!(
            persist(&mut store, &key, b"changed"),
            Err(StorageError::Conflict)
        );
        store.inject(Operation::Get, Fault::CorruptRead);
        assert_eq!(
            persist(&mut store, &key, b"archive"),
            Err(StorageError::Conflict)
        );
    }
}
