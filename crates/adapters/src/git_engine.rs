//! Native Git fallback. The caller owns and serializes the staging directory.
//! Archive publication and HTTP authorization remain application responsibilities.
use gitpickle_application::{
    ApplicationError, GitEngine, GitHttpRequest, GitInventory, Result, RetryAdvice,
};
use gitpickle_domain::ErrorCode;
use std::{
    collections::BTreeMap,
    io::{Read, Seek, Write},
    path::Path,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

#[derive(Default)]
pub struct NativeGitEngine;

fn failure(code: ErrorCode) -> ApplicationError {
    ApplicationError {
        code,
        message: "Cannot validate the staged Git repository".into(),
        retry: RetryAdvice::Never,
        next_action: "Check the staging repository and native Git installation".into(),
    }
}

impl NativeGitEngine {
    fn git(&self, staging: &Path, args: &[&str]) -> Result<String> {
        let mut command = Command::new("git");
        // Inherited Git configuration must not redirect inspection to another repo.
        let path = std::env::var_os("PATH").unwrap_or_default();
        command.env_clear().env("PATH", path);
        command.env("HOME", "/nonexistent");
        command.env("GIT_CONFIG_NOSYSTEM", "1");
        command.env("GIT_CONFIG_GLOBAL", "/dev/null");
        command.env("GIT_NO_REPLACE_OBJECTS", "1");
        command.env("GIT_TERMINAL_PROMPT", "0");
        command.env("GIT_ALLOW_PROTOCOL", "");
        let mut output =
            tempfile::tempfile().map_err(|_| failure(ErrorCode::OperationalFailure))?;
        let mut child = command
            .args([
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-C",
            ])
            .arg(staging)
            .args(args)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .stdout(
                output
                    .try_clone()
                    .map_err(|_| failure(ErrorCode::OperationalFailure))?,
            )
            .spawn()
            .map_err(|_| failure(ErrorCode::OperationalFailure))?;
        let started = Instant::now();
        let status = loop {
            let state = child.try_wait();
            let length = output.metadata().map(|m| m.len());
            if state.is_err()
                || length.is_err()
                || length.is_ok_and(|n| n > 64 * 1024 * 1024)
                || started.elapsed() > Duration::from_secs(3600)
            {
                let _ = child.kill();
                let _ = child.wait();
                return Err(failure(ErrorCode::OperationalFailure));
            }
            if let Ok(Some(status)) = state {
                break status;
            }
            std::thread::sleep(Duration::from_millis(20));
        };
        if !status.success() {
            return Err(failure(ErrorCode::IntegrityFailure));
        }
        output
            .rewind()
            .map_err(|_| failure(ErrorCode::OperationalFailure))?;
        let mut bytes = Vec::new();
        output
            .read_to_end(&mut bytes)
            .map_err(|_| failure(ErrorCode::OperationalFailure))?;
        String::from_utf8(bytes).map_err(|_| failure(ErrorCode::UnsupportedCapability))
    }
}

impl GitEngine for NativeGitEngine {
    fn inventory(&self, staging: &Path) -> Result<GitInventory> {
        let hash_algorithm = self.git(staging, &["rev-parse", "--show-object-format"])?;
        if !matches!(hash_algorithm.trim(), "sha1" | "sha256") {
            return Err(failure(ErrorCode::UnsupportedCapability));
        }
        let mut refs = BTreeMap::new();
        for line in self
            .git(
                staging,
                &["for-each-ref", "--format=%(refname) %(objectname)"],
            )?
            .lines()
        {
            let (name, oid) = line
                .split_once(' ')
                .ok_or_else(|| failure(ErrorCode::IntegrityFailure))?;
            refs.insert(name.into(), oid.into());
        }
        let (head, symbolic_head) = match self.git(staging, &["symbolic-ref", "-q", "HEAD"]) {
            Ok(head) => (head, true),
            Err(_) => (
                self.git(staging, &["rev-parse", "--verify", "HEAD"])?,
                false,
            ),
        };
        Ok(GitInventory {
            hash_algorithm: hash_algorithm.trim().into(),
            refs,
            head: head.trim().into(),
            symbolic_head,
        })
    }

    fn verify(&self, staging: &Path) -> Result<()> {
        self.git(staging, &["fsck", "--full", "--strict"])?;
        Ok(())
    }

    fn smart_http(
        &self,
        staging: &Path,
        request: &GitHttpRequest,
        sink: &mut dyn Write,
    ) -> Result<()> {
        if !matches!(
            (
                request.method.as_str(),
                request.endpoint.as_str(),
                request.query.as_str()
            ),
            ("GET", "info/refs", "service=git-upload-pack") | ("POST", "git-upload-pack", "")
        ) || !matches!(request.protocol.as_str(), "" | "version=1" | "version=2")
            || request.body.len() > 1024 * 1024
        {
            return Err(failure(ErrorCode::InvalidInput));
        }
        let staging = staging
            .canonicalize()
            .map_err(|_| failure(ErrorCode::InvalidInput))?;
        let mut input = tempfile::tempfile().map_err(|_| failure(ErrorCode::OperationalFailure))?;
        input
            .write_all(&request.body)
            .and_then(|_| input.rewind())
            .map_err(|_| failure(ErrorCode::OperationalFailure))?;
        let mut output =
            tempfile::tempfile().map_err(|_| failure(ErrorCode::OperationalFailure))?;
        let mut command = Command::new("git");
        command
            .env_clear()
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .env("HOME", "/nonexistent")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_NO_REPLACE_OBJECTS", "1")
            .env("GIT_PROJECT_ROOT", &staging)
            .env("GIT_HTTP_EXPORT_ALL", "1")
            .env("PATH_INFO", format!("/{}", request.endpoint))
            .env("REQUEST_METHOD", &request.method)
            .env("QUERY_STRING", &request.query)
            .env("CONTENT_TYPE", "application/x-git-upload-pack-request")
            .env("CONTENT_LENGTH", request.body.len().to_string())
            .env("GIT_PROTOCOL", &request.protocol)
            .args([
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "http.receivepack=false",
                "-c",
                "uploadpack.allowFilter=false",
                "-c",
                "uploadpack.allowAnySHA1InWant=false",
                "http-backend",
            ]);
        let mut child = command
            .stdin(input)
            .stdout(
                output
                    .try_clone()
                    .map_err(|_| failure(ErrorCode::OperationalFailure))?,
            )
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| failure(ErrorCode::OperationalFailure))?;
        let started = Instant::now();
        loop {
            if started.elapsed() > Duration::from_secs(120)
                || output
                    .metadata()
                    .map_or(true, |m| m.len() > 16 * 1024 * 1024 * 1024)
            {
                let _ = child.kill();
                let _ = child.wait();
                return Err(failure(ErrorCode::OperationalFailure));
            }
            match child.try_wait() {
                Ok(Some(status)) if status.success() => break,
                Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(failure(ErrorCode::OperationalFailure));
                }
            }
        }
        output
            .rewind()
            .map_err(|_| failure(ErrorCode::OperationalFailure))?;
        std::io::copy(&mut output, sink).map_err(|_| failure(ErrorCode::OperationalFailure))?;
        Ok(())
    }

    fn writable_hosting(&self) -> bool {
        false
    }
}

/// Private bridge used by the embedded daemon. Credentials never cross this bridge.
pub fn http_bridge(args: Vec<std::ffi::OsString>) -> std::result::Result<(), String> {
    if args.len() != 5 {
        return Err("Invalid Git request".into());
    }
    let mut body = Vec::new();
    std::io::stdin()
        .take(1024 * 1024 + 1)
        .read_to_end(&mut body)
        .map_err(|_| "Cannot read Git request")?;
    let request = GitHttpRequest {
        method: args[1].to_string_lossy().into_owned(),
        endpoint: args[2].to_string_lossy().into_owned(),
        query: args[3].to_string_lossy().into_owned(),
        protocol: args[4].to_string_lossy().into_owned(),
        body,
    };
    let engine: &dyn GitEngine = &NativeGitEngine;
    engine
        .smart_http(Path::new(&args[0]), &request, &mut std::io::stdout().lock())
        .map_err(|_| "Cannot serve Git request")?;
    Ok(())
}
