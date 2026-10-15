use std::{
    path::Path,
    process::{Command, ExitCode},
};

const CHECKS: &[&[&str]] = &[
    &["fmt", "--all", "--", "--check"],
    &[
        "clippy",
        "--workspace",
        "--all-targets",
        "--all-features",
        "--locked",
        "--",
        "-D",
        "warnings",
    ],
    &["test", "--workspace", "--all-features", "--locked"],
];

fn run(cargo: &std::ffi::OsStr, root: &Path, args: &[&str]) -> Result<(), String> {
    eprintln!("+ cargo {}", args.join(" "));
    let status = Command::new(cargo)
        .args(args)
        .current_dir(root)
        .status()
        .map_err(|error| format!("Cannot run cargo {}: {error}", args[0]))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("cargo {} failed: {status}", args[0]))
    }
}

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    if args.first().is_some_and(|arg| {
        ["fixtures", "bench", "perf-report", "model", "dev-env"]
            .iter()
            .any(|name| arg == name)
    }) {
        return match Command::new("python3")
            .arg(root.join(if args[0] == "dev-env" {
                "tools/gitpickle_dev_env.py"
            } else if args[0] == "model" {
                "tools/gitpickle_model.py"
            } else {
                "tools/gitpickle_perf.py"
            }))
            .args(if args[0] == "model" || args[0] == "dev-env" {
                &args[1..]
            } else {
                &args[..]
            })
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .current_dir(root)
            .status()
        {
            Ok(status) if status.success() => ExitCode::SUCCESS,
            Ok(_) => ExitCode::FAILURE,
            Err(error) => {
                eprintln!("Cannot run benchmark harness: {error}");
                ExitCode::FAILURE
            }
        };
    }
    if args.len() != 1 || args[0] != "check" {
        eprintln!("Usage: cargo xtask check|fixtures|bench|perf-report|model|dev-env [options]");
        return ExitCode::from(2);
    }
    let cargo = std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    for args in CHECKS {
        if let Err(error) = run(&cargo, root, args) {
            eprintln!("{error}");
            return ExitCode::FAILURE;
        }
    }
    match Command::new("python3")
        .args([
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tools",
            "-p",
            "test_*.py",
        ])
        .current_dir(root)
        .status()
    {
        Ok(status) if status.success() => {}
        _ => return ExitCode::FAILURE,
    }
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failed_child_is_an_error() {
        // Cargo must reject an unknown option without touching the workspace.
        let cargo = std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
        assert!(
            run(
                &cargo,
                Path::new(env!("CARGO_MANIFEST_DIR")),
                &["--invalid-xtask-test-option"]
            )
            .is_err()
        );
    }
}
