//! Publication tests use B2's actual capability declarations, without credentials.
use gitpickle_adapters::{
    recovery::download_archive,
    storage::{digest, emulator::*, providers::*, publication::*},
};
use gitpickle_application::storage::*;
use std::time::Duration;

fn store() -> Emulator {
    let provider = B2::new(
        Config::new("https://example.invalid", "test", "bucket", "owned/").unwrap(),
        Credentials::new("test".into(), "test".into(), None).unwrap(),
        Duration::from_secs(1),
    )
    .unwrap();
    assert!(!provider.capabilities().conditional_create);
    Emulator::new(provider.capabilities())
}
fn temporary() -> tempfile::TempDir {
    tempfile::Builder::new()
        .prefix(
            &std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "publication-test".into()),
        )
        .tempdir()
        .unwrap()
}
fn physical(logical: &str, bytes: &[u8]) -> ObjectKey {
    ObjectKey::new(format!("owned/{NAMESPACE}{logical}/{}", hex_digest(bytes))).unwrap()
}

#[test]
fn lost_response_retry_and_recovery_need_only_remote_objects() {
    let mut store = store();
    store.inject(Operation::Put, Fault::LostResponse);
    publish(&mut store, "owned/", "archive.json", b"identity").unwrap();
    store.calls.clear();
    publish(&mut store, "owned/", "archive.json", b"identity").unwrap();
    assert!(!store.calls.contains(&Operation::Put));
    let bytes = b"publication record";
    let logical = format!("repos/repo/commits/{}", hex_digest(bytes));
    publish(&mut store, "owned/", &logical, bytes).unwrap();
    let destination = temporary();
    store.inject(Operation::List, Fault::EmptyPage);
    download_archive(&mut store, "owned/", destination.path()).unwrap();
    assert_eq!(
        std::fs::read(destination.path().join("archive.json")).unwrap(),
        b"identity"
    );
    assert_eq!(
        std::fs::read(destination.path().join(logical)).unwrap(),
        bytes
    );
}

#[test]
fn failed_or_unverifiable_writes_never_succeed_and_retry_reconciles() {
    for fault in [Fault::Unavailable, Fault::CorruptRead, Fault::HiddenRead] {
        let mut store = store();
        if fault == Fault::Unavailable {
            store.inject(Operation::Put, fault);
        } else {
            store.inject(Operation::Put, Fault::LostResponse);
            store.inject(Operation::Get, fault);
        }
        assert!(publish(&mut store, "owned/", "archive.json", b"identity").is_err());
        publish(&mut store, "owned/", "archive.json", b"identity").unwrap();
    }
}

#[test]
fn conflicts_never_overwrite_and_recovery_rejects_split_identity() {
    let mut store = store();
    publish(&mut store, "owned/", "archive.json", b"first").unwrap();
    assert_eq!(
        publish(&mut store, "owned/", "archive.json", b"second"),
        Err(StorageError::Conflict)
    );
    assert_eq!(
        store
            .get(&physical("archive.json", b"first"))
            .unwrap()
            .bytes,
        b"first"
    );
    // Simulate a deployment violating single-writer exclusivity. Both records
    // survive; recovery must stop instead of picking a timestamp or list order.
    store
        .put(
            &physical("archive.json", b"second"),
            b"second",
            digest(b"second"),
            Condition::Any,
        )
        .unwrap();
    assert!(
        download_archive(&mut store, "owned/", temporary().path())
            .unwrap_err()
            .contains("Conflicting")
    );
}

#[test]
fn recovery_checks_identity_digest_and_legacy_conflicts() {
    let mut store = store();
    publish(&mut store, "owned/", "archive.json", b"identity").unwrap();
    store.inject(Operation::Get, Fault::CorruptRead);
    assert!(
        download_archive(&mut store, "owned/", temporary().path())
            .unwrap_err()
            .contains("digest")
    );
    let legacy = ObjectKey::new("owned/archive.json").unwrap();
    store
        .put(&legacy, b"other", digest(b"other"), Condition::Any)
        .unwrap();
    assert_eq!(
        publish(&mut store, "owned/", "archive.json", b"identity"),
        Err(StorageError::Conflict)
    );
    assert!(
        download_archive(&mut store, "owned/", temporary().path())
            .unwrap_err()
            .contains("Conflicting")
    );
}

#[test]
fn malformed_names_and_missing_visibility_fail_closed() {
    for name in [
        ".gitpickle-publication/v2/archive.json/abc",
        ".gitpickle-publication/v1/../abc",
        ".gitpickle-publication/v1/archive.json/xyz",
    ] {
        assert!(decode(name).is_err());
    }
    let mut caps = store().capabilities();
    caps.completed_write_visibility = false;
    let mut unavailable = Emulator::new(caps);
    assert_eq!(
        publish(&mut unavailable, "owned/", "archive.json", b"identity"),
        Err(StorageError::Unsupported)
    );
    assert!(unavailable.calls.is_empty());
}
