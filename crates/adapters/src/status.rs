//! Query the same evidence service from an installed executable.
use std::{ffi::OsString, process::Command};

pub fn run(args: Vec<OsString>) -> std::result::Result<u8, String> {
    let bootstrap = format!(
        "import sys, types\nsecurity = types.ModuleType('gitpickle_security')\nsys.modules[security.__name__] = security\nexec({:?}, security.__dict__)\na = types.ModuleType('gitpickle_archive')\nsys.modules[a.__name__] = a\nexec({:?}, a.__dict__)\nr = types.ModuleType('gitpickle_recovery')\nsys.modules[r.__name__] = r\nexec({:?}, r.__dict__)\ns = types.ModuleType('gitpickle_status')\nexec({:?}, s.__dict__)\nsys.exit(s.main())\n",
        include_str!("../../../tools/gitpickle_security.py"),
        include_str!("../../../tools/gitpickle_archive.py"),
        include_str!("../../../tools/gitpickle_recovery.py"),
        include_str!("../../../tools/gitpickle_status.py"),
    );
    let status = Command::new("python3")
        .args(["-I", "-B", "-c", &bootstrap])
        .args(args)
        .env_remove("GITPICKLE_STORAGE_ACCESS_KEY")
        .env_remove("GITPICKLE_STORAGE_SECRET_KEY")
        .env_remove("GITPICKLE_STORAGE_SESSION_TOKEN")
        .status()
        .map_err(|_| {
            "operational_failure: Install Python 3 to query archive evidence".to_string()
        })?;
    Ok(status
        .code()
        .and_then(|code| u8::try_from(code).ok())
        .unwrap_or(5))
}
