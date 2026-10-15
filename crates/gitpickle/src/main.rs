use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.is_empty()
        || args[0].to_string_lossy().starts_with("--")
            && args[0] != "--help"
            && args[0] != "--version"
    {
        args.insert(0, "status".into());
    }
    if args.len() == 2 && args[0] == "help" && args[1] == "--all" {
        print!("{}", gitpickle_interfaces::HELP_ALL);
        ExitCode::SUCCESS
    } else if args.len() == 1 && (args[0] == "--help" || args[0] == "help") {
        print!("{}", gitpickle_interfaces::HELP);
        ExitCode::SUCCESS
    } else if args.len() == 1 && args[0] == "--version" {
        println!("gitpickle {}", env!("CARGO_PKG_VERSION"));
        ExitCode::SUCCESS
    } else if args[0] == "__git-http" {
        match gitpickle_adapters::git_engine::http_bridge(args[1..].to_vec()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("{error}");
                ExitCode::from(5)
            }
        }
    } else if args[0] == "__archive-store" {
        match gitpickle_adapters::archive_store::run(args[1..].to_vec()) {
            Ok(code) => ExitCode::from(code),
            Err(_) => ExitCode::from(5),
        }
    } else if args[0] == "setup" {
        match gitpickle_adapters::daemon::setup(args[1..].to_vec()) {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                eprintln!("{error}");
                ExitCode::from(5)
            }
        }
    } else if args[0] == "tui" {
        match gitpickle_adapters::daemon::tui(args[1..].to_vec()) {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                eprintln!("{error}");
                ExitCode::from(5)
            }
        }
    } else if args[0] == "mcp" {
        match gitpickle_adapters::daemon::mcp(args[1..].to_vec()) {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                eprintln!("{error}");
                ExitCode::from(5)
            }
        }
    } else if args[0] == "serve" {
        match gitpickle_adapters::daemon::run(args[1..].to_vec()) {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                eprintln!("{error}");
                ExitCode::from(5)
            }
        }
    } else if matches!(
        args[0].to_str(),
        Some(
            "protect" | "backup" | "sync" | "jobs" | "hosting" | "portfolio" | "points" | "history"
        )
    ) || args[0] == "restore"
        && !args.iter().any(|a| a == "--from-storage" || a == "--help")
        || matches!(args[0].to_str(), Some("status" | "doctor"))
            && (args.iter().any(|a| a == "--server")
                || std::env::var_os("GITPICKLE_SERVER").is_some())
    {
        match gitpickle_adapters::daemon::workflow(args) {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                eprintln!("{error}");
                ExitCode::from(5)
            }
        }
    } else if args[0] == "status" || args[0] == "doctor" {
        let json = args.iter().any(|arg| arg == "--json");
        match gitpickle_adapters::status::run(args) {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                if json {
                    use gitpickle_interfaces::machine::*;
                    let result = ResultV1::<()> {
                        schema_version: SCHEMA_VERSION,
                        observed_at_unix_seconds: std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs(),
                        scope: Scope {
                            repository_ids: vec![],
                            archive_id: None,
                        },
                        outcome: Outcome::Failed,
                        job_id: None,
                        coverage_gaps: vec![],
                        evidence_ids: vec![],
                        next_action: Some(NextAction {
                            operation: "install_runtime".into(),
                            arguments: "{}".parse().expect("empty argument object"),
                        }),
                        data: None,
                        error: Some(Failure {
                            code: "operational_failure".into(),
                            message: error,
                            retry: Retry::Never,
                        }),
                    };
                    print!("{}", result.to_json_line().expect("valid status failure"));
                } else {
                    eprintln!("{error}");
                }
                ExitCode::from(5)
            }
        }
    } else if args[0] == "restore" && args.iter().any(|a| a == "--from-storage" || a == "--help") {
        match gitpickle_adapters::recovery::run(args[1..].to_vec()) {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                eprintln!("Recovery failed: {error}");
                ExitCode::FAILURE
            }
        }
    } else {
        eprintln!("Unsupported command. Run gitpickle --help for available commands.");
        ExitCode::from(2)
    }
}
