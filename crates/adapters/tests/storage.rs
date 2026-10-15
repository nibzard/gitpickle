use gitpickle_adapters::storage::{
    conformance::*, digest, emulator::*, put_immutable, verified_get,
};
use gitpickle_application::storage::*;

fn key(name: &str) -> ObjectKey {
    ObjectKey::new(format!("test/{name}")).unwrap()
}
fn scope(run: &str) -> TestScope {
    TestScope::new(
        &std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "local-test".into()),
        run,
    )
    .unwrap()
}
#[test]
fn emulator_passes_shared_provider_contract() {
    let mut store = Emulator::default();
    let report = run(&mut store, &scope("contract")).unwrap();
    assert_eq!(report.checked.len(), 6);
    assert!(report.unsupported.is_empty());
    assert_eq!(
        store.capabilities().retention,
        RetentionCapability::Unsupported
    );
    assert!(!store.capabilities().versioning);
}
#[test]
fn existing_archives_and_occupied_scopes_are_preserved() {
    let mut store = Emulator::default();
    let archive = ObjectKey::new("archive/repos/existing").unwrap();
    let revision = put_immutable(&mut store, &archive, b"retained", 1).unwrap();
    let scope = scope("isolated");
    run(&mut store, &scope).unwrap();
    assert_eq!(store.get(&archive).unwrap().revision, revision);
    let before = store.calls.len();
    assert_eq!(run(&mut store, &scope), Err(StorageError::Conflict));
    assert_eq!(
        &store.calls[before..],
        &[
            Operation::List,
            Operation::List,
            Operation::List,
            Operation::List
        ]
    );
    for bad in ["", "../archive", "a/b", ".", "a\nb"] {
        assert!(TestScope::new(bad, "run").is_err());
    }
}
#[test]
fn immutable_retry_reconciles_lost_responses_and_never_overwrites() {
    let mut store = Emulator::default();
    store.inject(Operation::Put, Fault::LostResponse);
    let revision = put_immutable(&mut store, &key("lost"), b"payload", 3).unwrap();
    assert_eq!(store.calls, vec![Operation::Put, Operation::Get]);
    assert_eq!(
        put_immutable(&mut store, &key("lost"), b"payload", 3),
        Ok(revision)
    );
    assert_eq!(
        put_immutable(&mut store, &key("lost"), b"other", 3),
        Err(StorageError::Integrity)
    );
    assert_eq!(store.get(&key("lost")).unwrap().bytes, b"payload");
}
#[test]
fn retry_budget_is_bounded_and_corruption_is_not_retried() {
    let mut store = Emulator::default();
    for _ in 0..3 {
        store.inject(Operation::Put, Fault::Unavailable);
    }
    assert_eq!(
        put_immutable(&mut store, &key("retry"), b"ok", 3),
        Err(StorageError::UnknownOutcome)
    );
    assert_eq!(store.calls.len(), 3);
    assert!(put_immutable(&mut store, &key("retry"), b"ok", 1).is_ok());
    store.inject(Operation::Get, Fault::CorruptRead);
    assert_eq!(
        verified_get(&mut store, &key("retry"), digest(b"ok")),
        Err(StorageError::Integrity)
    );
    assert!(verified_get(&mut store, &key("retry"), digest(b"ok")).is_ok());
    assert_eq!(
        store.put(&key("bad"), b"bad", digest(b"good"), Condition::Any),
        Err(StorageError::Integrity)
    );
}
#[test]
fn pagination_handles_empty_pages_and_rejects_loops_and_cross_prefix_cursors() {
    let mut store = Emulator::default();
    for name in ["a", "b", "c"] {
        put_immutable(&mut store, &key(name), b"x", 1).unwrap();
    }
    store.inject(Operation::List, Fault::EmptyPage);
    assert_eq!(list_all(&mut store, "test/", 1).unwrap().len(), 3);
    store.inject(Operation::List, Fault::EmptyPage);
    store.inject(Operation::List, Fault::EmptyPage);
    assert_eq!(
        list_all(&mut store, "test/", 1),
        Err(StorageError::InvalidCursor)
    );
    let cursor = store.list("test/", None, 1).unwrap().next.unwrap();
    assert_eq!(
        store.list("else/", Some(&cursor), 1),
        Err(StorageError::InvalidCursor)
    );
    assert_eq!(
        store.list("test/", None, 0),
        Err(StorageError::InvalidInput)
    );
}
#[test]
fn lost_multipart_completion_is_reconciled_by_digest() {
    let mut store = Emulator::default();
    store.inject(Operation::Complete, Fault::LostResponse);
    run(&mut store, &scope("multipart-lost")).unwrap();
}
#[test]
fn multipart_retries_parts_and_checks_completion_conditions() {
    let mut store = Emulator::default();
    let key = key("multipart");
    let upload = store.begin(&key, digest(b"ab"), Condition::Absent).unwrap();
    store.inject(Operation::Part, Fault::LostResponse);
    assert_eq!(
        store.part(&upload, 1, b"a"),
        Err(StorageError::UnknownOutcome)
    );
    store.part(&upload, 1, b"a").unwrap();
    assert_eq!(
        store.complete(&upload, &[1, 2]),
        Err(StorageError::InvalidInput)
    );
    store.part(&upload, 2, b"b").unwrap();
    assert_eq!(
        store.complete(&upload, &[2, 1]),
        Err(StorageError::InvalidInput)
    );
    assert_eq!(store.complete(&upload, &[1]), Err(StorageError::Integrity));
    put_immutable(&mut store, &key, b"existing", 1).unwrap();
    assert_eq!(
        store.complete(&upload, &[1, 2]),
        Err(StorageError::Conflict)
    );
    store.abort(&upload).unwrap();
    assert_eq!(store.get(&key).unwrap().bytes, b"existing");
    let mut foreign = upload.clone();
    foreign.key = ObjectKey::new("other/key").unwrap();
    assert_eq!(store.part(&foreign, 1, b"a"), Err(StorageError::NotFound));
}
#[test]
fn harness_rejects_broken_visibility_and_reports_optional_capabilities() {
    let mut store = Emulator::default();
    store.inject(Operation::Get, Fault::HiddenRead);
    assert_eq!(
        run(&mut store, &scope("hidden")),
        Err(StorageError::NotFound)
    );
    let mut caps = store.capabilities();
    caps.multipart = false;
    caps.conditional_replace = false;
    let mut store = Emulator::new(caps);
    assert_eq!(
        run(&mut store, &scope("limited")).unwrap().unsupported,
        vec!["conditional replace", "multipart"]
    );
    caps.conditional_create = false;
    let mut store = Emulator::new(caps);
    assert_eq!(
        run(&mut store, &scope("unsafe")),
        Err(StorageError::Unsupported)
    );
    assert!(store.calls.is_empty());
    caps.completed_write_visibility = false;
    assert_eq!(
        run(&mut Emulator::new(caps), &scope("eventual")),
        Err(StorageError::Unsupported)
    );
}

#[test]
fn harness_rejects_corrupted_readback_with_unchanged_revision() {
    let mut store = Emulator::default();
    store.inject(Operation::Get, Fault::CorruptRead);
    assert_eq!(
        run(&mut store, &scope("corrupt")),
        Err(StorageError::Integrity)
    );
}

#[test]
fn lost_write_and_failed_readback_reconcile_on_the_next_attempt() {
    let mut store = Emulator::default();
    store.inject(Operation::Put, Fault::LostResponse);
    store.inject(Operation::Get, Fault::Unavailable);
    assert!(put_immutable(&mut store, &key("reconcile"), b"content", 2).is_ok());
    assert_eq!(
        store.calls,
        vec![
            Operation::Put,
            Operation::Get,
            Operation::Put,
            Operation::Get
        ]
    );
    assert_eq!(store.get(&key("reconcile")).unwrap().bytes, b"content");
}

#[test]
fn digest_is_sha256_and_keys_reject_traversal() {
    assert_eq!(
        digest(b"abc").0,
        [
            0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae,
            0x22, 0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61,
            0xf2, 0x00, 0x15, 0xad,
        ]
    );
    for invalid in ["", "/a", "a/", "a//b", "a/../b", "a/./b", "a\\b", "a\nb"] {
        assert_eq!(ObjectKey::new(invalid), Err(StorageError::InvalidInput));
    }
}

#[test]
fn nested_disposable_scopes_reject_unsafe_bases_and_multipart_collisions() {
    for base in [
        "",
        "archive",
        "../archive/",
        "archive//",
        "archive/../test/",
    ] {
        assert!(TestScope::within(base, "looper_test", "run").is_err());
    }
    let scope = TestScope::within("development/", "looper_test", "run").unwrap();
    assert_eq!(scope.prefix(), "development/looper_test/conformance/run/");
    let mut store = Emulator::default();
    let key = ObjectKey::new(format!("{}unconditional-multipart", scope.prefix())).unwrap();
    let original = put_immutable(&mut store, &key, b"existing", 1).unwrap();
    let before = store.calls.len();
    assert_eq!(
        probe_unconditional_multipart(&mut store, &scope),
        Err(StorageError::Integrity)
    );
    assert_eq!(&store.calls[before..], &[Operation::Get]);
    assert_eq!(store.get(&key).unwrap().revision, original);
}
