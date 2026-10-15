//! Read-only archive recovery through the embedded version 1 reference reader.
use crate::storage::{digest, providers::*};
use gitpickle_application::storage::*;
use std::{collections::BTreeSet, ffi::OsString, fs, path::Path, process::Command, time::Duration};

/// Materialize archive records without local indexes or provider writes.
/// This bounded implementation downloads the archive prefix before selection.
pub fn download_archive(
    store: &mut dyn Storage,
    prefix: &str,
    destination: &Path,
) -> std::result::Result<(), String> {
    ObjectKey::new(prefix.trim_end_matches('/')).map_err(|e| format!("{e:?}"))?;
    if !prefix.ends_with('/') || prefix.ends_with("//") {
        return Err("Invalid archive prefix".into());
    }
    let mut cursor = None;
    let mut seen = BTreeSet::new();
    loop {
        let page = store
            .list(prefix, cursor.as_deref(), 1000)
            .map_err(|e| format!("Archive listing failed: {e:?}"))?;
        for key in page.keys {
            let relative = key
                .as_str()
                .strip_prefix(prefix)
                .ok_or("Object outside archive prefix")?;
            // Local caches, locks, and interrupted uploads are never recovery inputs.
            if relative != "archive.json"
                && ![
                    "repos/",
                    "ownership/",
                    "control/",
                    "jobs/",
                    "protection/",
                    "execution/",
                ]
                .iter()
                .any(|p| relative.starts_with(p))
            {
                continue;
            }
            ObjectKey::new(relative).map_err(|_| "Invalid archive key")?;
            let object = store
                .get(&key)
                .map_err(|e| format!("Archive read failed: {e:?}"))?;
            if relative != "archive.json" {
                let actual: String = digest(&object.bytes)
                    .0
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect();
                if relative.rsplit('/').next() != Some(actual.as_str()) {
                    return Err("Archive digest mismatch".into());
                }
            }
            let path = destination.join(relative);
            fs::create_dir_all(path.parent().ok_or("Invalid archive path")?)
                .map_err(|e| e.to_string())?;
            fs::write(path, object.bytes).map_err(|e| e.to_string())?;
        }
        match page.next {
            None => break,
            Some(next) => {
                if !seen.insert(next.clone()) {
                    return Err("Repeated archive listing cursor".into());
                }
                cursor = Some(next);
            }
        }
    }
    Ok(())
}

fn option(args: &mut Vec<OsString>, name: &str) -> std::result::Result<String, String> {
    let index = args
        .iter()
        .position(|a| a == name)
        .ok_or_else(|| format!("Missing {name}"))?;
    args.remove(index);
    if index == args.len() || args[index].to_string_lossy().starts_with("--") {
        return Err(format!("Missing value for {name}"));
    }
    args.remove(index)
        .into_string()
        .map_err(|_| format!("Invalid {name}"))
}

pub fn run(mut args: Vec<OsString>) -> std::result::Result<u8, String> {
    let temporary = tempfile::Builder::new()
        .prefix(&format!(
            "{}-recovery-",
            std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "gitpickle".into())
        ))
        .tempdir()
        .map_err(|e| e.to_string())?;
    let mut location = None;
    if args.iter().any(|a| a == "--provider") {
        let provider = option(&mut args, "--provider")?;
        let endpoint = option(&mut args, "--endpoint")?;
        let region = option(&mut args, "--region")?;
        let bucket = option(&mut args, "--bucket")?;
        let prefix = option(&mut args, "--prefix")?;
        location = Some(serde_json::json!({
            "provider": provider, "endpoint": endpoint, "region": region,
            "bucket": bucket, "prefix": prefix
        }));
        let config = Config::new(&endpoint, &region, &bucket, &prefix)
            .map_err(|e| format!("Storage configuration: {e:?}"))?;
        let credentials = Credentials::new(
            std::env::var("GITPICKLE_STORAGE_ACCESS_KEY")
                .map_err(|_| "Missing GITPICKLE_STORAGE_ACCESS_KEY")?,
            std::env::var("GITPICKLE_STORAGE_SECRET_KEY")
                .map_err(|_| "Missing GITPICKLE_STORAGE_SECRET_KEY")?,
            std::env::var("GITPICKLE_STORAGE_SESSION_TOKEN").ok(),
        )
        .map_err(|e| format!("Storage credentials: {e:?}"))?;
        let timeout = Duration::from_secs(60);
        let mut store: Box<dyn Storage> = match provider.as_str() {
            "r2" => Box::new(
                R2::new(config, credentials, timeout).map_err(|e| format!("Storage: {e:?}"))?,
            ),
            "s3" => Box::new(
                S3::new(config, credentials, timeout).map_err(|e| format!("Storage: {e:?}"))?,
            ),
            "b2" => Box::new(
                B2::new(config, credentials, timeout).map_err(|e| format!("Storage: {e:?}"))?,
            ),
            _ => return Err("Provider must be r2, s3, or b2".into()),
        };
        let index = args
            .iter()
            .position(|a| a == "--from-storage")
            .ok_or("Missing --from-storage")?;
        download_archive(store.as_mut(), &prefix, temporary.path())?;
        args.insert(index + 1, temporary.path().as_os_str().to_owned());
    }
    // Embed both modules. Recovery from an installed binary needs no source checkout.
    let bootstrap = format!(
        "import sys, types\nsecurity = types.ModuleType('gitpickle_security')\nsys.modules[security.__name__] = security\nexec({:?}, security.__dict__)\na = types.ModuleType('gitpickle_archive')\nsys.modules[a.__name__] = a\nexec({:?}, a.__dict__)\nr = types.ModuleType('gitpickle_recovery')\nexec({:?}, r.__dict__)\nsys.exit(r.main())\n",
        include_str!("../../../tools/gitpickle_security.py"),
        include_str!("../../../tools/gitpickle_archive.py"),
        include_str!("../../../tools/gitpickle_recovery.py"),
    );
    let mut command = Command::new("python3");
    command
        .args(["-I", "-B", "-c", &bootstrap])
        .args(args)
        .env_remove("GITPICKLE_STORAGE_ACCESS_KEY")
        .env_remove("GITPICKLE_STORAGE_SECRET_KEY")
        .env_remove("GITPICKLE_STORAGE_SESSION_TOKEN")
        .env_remove("GITPICKLE_RECOVERY_LOCATION");
    if let Some(location) = location {
        command.env("GITPICKLE_RECOVERY_LOCATION", location.to_string());
    }
    let status = command
        .status()
        .map_err(|e| format!("Cannot run recovery reader (requires Python 3): {e}"))?;
    Ok(status
        .code()
        .and_then(|code| u8::try_from(code).ok())
        .unwrap_or(1))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::emulator::{Emulator, Fault, Operation};

    #[test]
    fn storage_rebuild_reads_every_page_and_ignores_caches() {
        let mut store = Emulator::default();
        let prefix = "owned/archive/";
        for index in 0..1002 {
            let bytes = format!("record-{index}").into_bytes();
            let hash: String = digest(&bytes)
                .0
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            let key = ObjectKey::new(format!("{prefix}control/{hash}")).unwrap();
            store
                .put(&key, &bytes, digest(&bytes), Condition::Absent)
                .unwrap();
        }
        let key = ObjectKey::new(format!("{prefix}.projection/database")).unwrap();
        store
            .put(
                &key,
                b"untrusted cache",
                digest(b"untrusted cache"),
                Condition::Absent,
            )
            .unwrap();
        store.calls.clear();
        store.inject(Operation::List, Fault::EmptyPage);
        let temp = tempfile::Builder::new()
            .prefix(
                &std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "recovery-test".into()),
            )
            .tempdir()
            .unwrap();
        download_archive(&mut store, prefix, temp.path()).unwrap();
        assert_eq!(
            fs::read_dir(temp.path().join("control")).unwrap().count(),
            1002
        );
        assert!(!temp.path().join(".projection").exists());
        assert!(
            store
                .calls
                .iter()
                .filter(|op| **op == Operation::List)
                .count()
                >= 3
        );
        assert!(
            store
                .calls
                .iter()
                .all(|op| matches!(op, Operation::List | Operation::Get))
        );
    }

    #[test]
    fn download_detects_corrupt_provider_bytes() {
        let mut store = Emulator::default();
        let bytes = b"record";
        let hash: String = digest(bytes).0.iter().map(|b| format!("{b:02x}")).collect();
        let key = ObjectKey::new(format!("owned/control/{hash}")).unwrap();
        store
            .put(&key, bytes, digest(bytes), Condition::Absent)
            .unwrap();
        store.inject(Operation::Get, Fault::CorruptRead);
        let temp = tempfile::Builder::new()
            .prefix(
                &std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "recovery-test".into()),
            )
            .tempdir()
            .unwrap();
        assert!(
            download_archive(&mut store, "owned/", temp.path())
                .unwrap_err()
                .contains("digest")
        );
    }
}
