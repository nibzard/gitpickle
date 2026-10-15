use gitpickle_adapters::git_engine::NativeGitEngine;
use gitpickle_application::GitEngine;
use std::{path::Path, process::Command};

fn git(path: &Path, args: &[&str]) -> String {
    let output = Command::new("git")
        .arg("-C")
        .arg(path)
        .args(args)
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output.stderr);
    String::from_utf8(output.stdout).unwrap().trim().into()
}

#[test]
fn native_engine_preserves_formats_refs_and_unborn_head() {
    let engine: &dyn GitEngine = &NativeGitEngine;
    assert!(!engine.writable_hosting());
    for format in ["sha1", "sha256"] {
        let dir = tempfile::tempdir().unwrap();
        git(
            dir.path(),
            &[
                "init",
                "--bare",
                "--template=",
                "--initial-branch=main",
                &format!("--object-format={format}"),
            ],
        );
        let inventory = engine.inventory(dir.path()).unwrap();
        assert_eq!(inventory.hash_algorithm, format);
        assert_eq!(inventory.head, "refs/heads/main");
        assert!(inventory.symbolic_head);
        assert!(inventory.refs.is_empty());
        engine.verify(dir.path()).unwrap();
        let tree = git(
            dir.path(),
            &["hash-object", "-t", "tree", "-w", "/dev/null"],
        );
        let commit = git(
            dir.path(),
            &[
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit-tree",
                &tree,
                "-m",
                "fixture",
            ],
        );
        git(dir.path(), &["update-ref", "refs/heads/main", &commit]);
        git(dir.path(), &["update-ref", "refs/tags/kept", &commit]);
        let inventory = engine.inventory(dir.path()).unwrap();
        assert_eq!(inventory.refs.len(), 2);
        assert_eq!(inventory.refs["refs/heads/main"], commit);
        git(dir.path(), &["update-ref", "--no-deref", "HEAD", &commit]);
        assert!(!engine.inventory(dir.path()).unwrap().symbolic_head);
        std::fs::remove_file(
            dir.path()
                .join("objects")
                .join(&commit[..2])
                .join(&commit[2..]),
        )
        .unwrap();
        assert!(engine.verify(dir.path()).is_err());
    }
}

#[test]
fn http_engine_refuses_writes_and_unknown_protocols() {
    use gitpickle_application::GitHttpRequest;
    let engine: &dyn GitEngine = &NativeGitEngine;
    let dir = tempfile::tempdir().unwrap();
    git(dir.path(), &["init", "--bare", "--template="]);
    for (method, endpoint, query, protocol) in [
        ("POST", "git-receive-pack", "", ""),
        ("GET", "info/refs", "service=git-receive-pack", ""),
        ("GET", "HEAD", "", ""),
        ("GET", "info/refs", "service=git-upload-pack", "version=3"),
    ] {
        assert!(
            engine
                .smart_http(
                    dir.path(),
                    &GitHttpRequest {
                        method: method.into(),
                        endpoint: endpoint.into(),
                        query: query.into(),
                        protocol: protocol.into(),
                        body: vec![],
                    },
                    &mut std::io::sink()
                )
                .is_err()
        );
    }
}
