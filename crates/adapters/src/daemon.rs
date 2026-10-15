//! Compose the embedded authenticated management service.
use std::{ffi::OsString, process::Command};

pub fn run(args: Vec<OsString>) -> std::result::Result<u8, String> {
    launch(args, "gitpickle_daemon")
}

pub fn workflow(args: Vec<OsString>) -> std::result::Result<u8, String> {
    launch(args, "gitpickle_workflow")
}

pub fn mcp(args: Vec<OsString>) -> std::result::Result<u8, String> {
    launch(args, "gitpickle_mcp")
}

fn launch(args: Vec<OsString>, entry: &str) -> std::result::Result<u8, String> {
    let modules = [
        (
            "gitpickle_security",
            include_str!("../../../tools/gitpickle_security.py"),
        ),
        (
            "gitpickle_archive",
            include_str!("../../../tools/gitpickle_archive.py"),
        ),
        (
            "gitpickle_writer",
            include_str!("../../../tools/gitpickle_writer.py"),
        ),
        (
            "gitpickle_recovery",
            include_str!("../../../tools/gitpickle_recovery.py"),
        ),
        (
            "gitpickle_plans",
            include_str!("../../../tools/gitpickle_plans.py"),
        ),
        (
            "gitpickle_status",
            include_str!("../../../tools/gitpickle_status.py"),
        ),
        (
            "gitpickle_jobs",
            include_str!("../../../tools/gitpickle_jobs.py"),
        ),
        (
            "gitpickle_github",
            include_str!("../../../tools/gitpickle_github.py"),
        ),
        (
            "gitpickle_workflow",
            include_str!("../../../tools/gitpickle_workflow.py"),
        ),
        (
            "gitpickle_cloud",
            include_str!("../../../tools/gitpickle_cloud.py"),
        ),
        (
            "gitpickle_agent",
            include_str!("../../../tools/gitpickle_agent.py"),
        ),
        (
            "gitpickle_mcp",
            include_str!("../../../tools/gitpickle_mcp.py"),
        ),
        (
            "gitpickle_daemon",
            include_str!("../../../tools/gitpickle_daemon.py"),
        ),
    ];
    let mut bootstrap =
        "import sys, types, os\nos.unlink(__file__)\nos.rmdir(os.path.dirname(__file__))\n"
            .to_string();
    for (name, source) in modules {
        bootstrap.push_str(&format!(
            "m = types.ModuleType({name:?})\nsys.modules[m.__name__] = m\nexec({source:?}, m.__dict__)\n"
        ));
    }
    bootstrap.push_str(&format!("sys.exit(sys.modules[{entry:?}].main())\n"));
    // The embedded modules exceed Linux's per-argument limit. Python removes
    // this private file and its directory immediately after loading the source.
    let temporary = tempfile::Builder::new()
        .prefix(&format!(
            "{}-runtime-",
            std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "gitpickle".into())
        ))
        .tempdir()
        .map_err(|_| "Cannot create private runtime staging".to_string())?;
    let script = temporary.path().join("runtime.py");
    std::fs::write(&script, bootstrap)
        .map_err(|_| "Cannot write private runtime staging".to_string())?;
    let mut command = Command::new("python3");
    command.args(["-I", "-B"]).arg(&script).args(args);
    command.env("GITPICKLE_SOFTWARE_VERSION", env!("CARGO_PKG_VERSION"));
    command.env(
        "GITPICKLE_EXECUTABLE",
        std::env::current_exe().map_err(|_| "Cannot locate executable".to_string())?,
    );
    // Keep the service PID stable and deliver shutdown signals to the listener.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let _error = command.exec();
        Err("Cannot start management service. Install Python 3.".to_string())
    }
    #[cfg(not(unix))]
    {
        let status = command
            .status()
            .map_err(|_| "Cannot start management service. Install Python 3.".to_string())?;
        Ok(status
            .code()
            .and_then(|code| u8::try_from(code).ok())
            .unwrap_or(5))
    }
}
