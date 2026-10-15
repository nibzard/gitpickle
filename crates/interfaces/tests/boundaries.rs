//! Check Cargo's resolved graph, including target, build, and test dependencies.
use serde_json::Value;
use std::{fs, path::Path, process::Command};

fn allowed(package: &str, dependency: &str) -> bool {
    match package {
        "gitpickle-domain" => false,
        "gitpickle-application" => dependency == "gitpickle-domain",
        "gitpickle-interfaces" => {
            matches!(dependency, "gitpickle-application" | "serde" | "serde_json")
        }
        "gitpickle-adapters" => {
            !dependency.starts_with("gitpickle")
                || matches!(dependency, "gitpickle-domain" | "gitpickle-application")
        }
        "gitpickle" => {
            !dependency.starts_with("gitpickle")
                || matches!(
                    dependency,
                    "gitpickle-application" | "gitpickle-adapters" | "gitpickle-interfaces"
                )
        }
        "xtask" => !dependency.starts_with("gitpickle"),
        _ => false,
    }
}
#[test]
fn dependencies_point_inward() {
    let output = Command::new(env!("CARGO"))
        .args([
            "metadata",
            "--format-version",
            "1",
            "--locked",
            "--offline",
            "--no-deps",
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let metadata: Value = serde_json::from_slice(&output.stdout).unwrap();
    for package in metadata["packages"].as_array().unwrap() {
        let name = package["name"].as_str().unwrap();
        for dependency in package["dependencies"].as_array().unwrap() {
            let dep = dependency["name"].as_str().unwrap();
            assert!(allowed(name, dep), "forbidden dependency: {name} -> {dep}");
        }
    }
}
#[test]
fn guard_rejects_provider_database_transport_and_reverse_dependencies() {
    for dep in [
        "aws-sdk-s3",
        "rusqlite",
        "axum",
        "serde",
        "gitpickle-adapters",
    ] {
        assert!(!allowed("gitpickle-domain", dep));
        assert!(!allowed("gitpickle-application", dep));
    }
    assert!(!allowed("gitpickle-interfaces", "gitpickle-adapters"));
    assert!(!allowed("gitpickle-adapters", "gitpickle-interfaces"));
}
fn inspect(path: &Path) {
    for entry in fs::read_dir(path).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            inspect(&path);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            let source = fs::read_to_string(&path).unwrap();
            let tokens: Vec<_> = source
                .split(|c: char| !c.is_alphanumeric() && c != '_')
                .collect();
            for forbidden in [
                "reqwest",
                "axum",
                "rusqlite",
                "sqlx",
                "aws_sdk_s3",
                "http",
                "tokio",
                "rmcp",
                "terminal",
                "ETag",
            ] {
                assert!(
                    !tokens.contains(&forbidden),
                    "{} contains boundary type {forbidden}",
                    path.display()
                );
            }
            for forbidden in [
                "std::fs",
                "std::net",
                "std::process",
                "include!",
                "include_str!",
                "include_bytes!",
            ] {
                assert!(
                    !source.contains(forbidden),
                    "{} contains {forbidden}",
                    path.display()
                );
            }
        }
    }
}
#[test]
fn domain_sources_have_no_infrastructure_types() {
    inspect(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../domain/src"));
}
