use std::process::{Command, Output};

fn cli(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_gitpickle"))
        .args(args)
        .output()
        .expect("run the built executable")
}

#[test]
fn help_is_available_without_configuration() {
    for args in [vec!["--help"], vec!["help"]] {
        let output = cli(&args);
        assert!(output.status.success());
        assert!(output.stderr.is_empty());
        let text = String::from_utf8(output.stdout).unwrap();
        assert!(text.contains("Usage: gitpickle"));
        assert!(text.contains("protect github"));
        assert!(text.contains("gitpickle help --all"));
        assert!(!text.contains("GITPICKLE_MANAGEMENT_TOKENS"));
    }
}

#[test]
fn version_matches_package() {
    let output = cli(&["--version"]);
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        format!("gitpickle {}\n", env!("CARGO_PKG_VERSION"))
    );
}

#[test]
fn unsupported_commands_never_report_success() {
    for args in [vec!["--help", "backup"], vec!["--version", "extra"]] {
        let output = cli(&args);
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        assert!(
            String::from_utf8(output.stderr)
                .unwrap()
                .contains("Unsupported command")
        );
    }
}

#[test]
fn equals_form_options_select_recovery_and_management_handlers() {
    let temporary = tempfile::tempdir().unwrap();
    std::fs::write(
        temporary.path().join("archive.json"),
        r#"{"kind":"archive","archive_id":"equals-form","encryption":"none","version":1}"#,
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_gitpickle"))
        .args([
            "restore",
            &format!("--from-storage={}", temporary.path().display()),
            "--inventory",
            "--json",
        ])
        .env_remove("GITPICKLE_SERVER")
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    assert!(
        String::from_utf8(output.stdout)
            .unwrap()
            .contains(r#""archive_id": "equals-form""#)
    );
    for command in ["status", "doctor"] {
        let output = Command::new(env!("CARGO_BIN_EXE_gitpickle"))
            .args([command, "--server=http://127.0.0.1:1", "--help"])
            .env_remove("GITPICKLE_SERVER")
            .output()
            .unwrap();
        assert!(output.status.success(), "{:?}", output);
        assert!(
            String::from_utf8(output.stdout)
                .unwrap()
                .contains("--server")
        );
    }
}

#[test]
fn installed_binary_recovers_without_checkout_source_or_database() {
    let temporary = tempfile::Builder::new()
        .prefix(&std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "recovery-cli".into()))
        .tempdir()
        .unwrap();
    let root = temporary.path();
    let tools = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tools");
    let fixture = Command::new("python3")
        .args(["-B", "-c", r#"
import shutil
from pathlib import Path
import gitpickle_archive as a
root = Path.cwd()
store = root / 'archive'
a.initialize(store, 'test-cli')
repo = root / 'source.git'
repo.mkdir()
a.git(repo, 'init', '--bare', '--template=', '--initial-branch=main')
payload = b'CLI required LFS bytes'
oid = a.digest(payload)
lfs = root / 'lfs'
lfs.mkdir()
(lfs / oid).write_bytes(payload)
(root / 'lfs-oid').write_text(oid)
pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n'.encode()
blob = a.git(repo, 'hash-object', '-w', '--stdin', data=pointer).decode().strip()
tree = a.git(repo, 'mktree', data=f'100644 blob {blob}\tlarge.bin\n'.encode()).decode().strip()
commit = a.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit-tree', tree, data=b'history').decode().strip()
a.git(repo, 'update-ref', 'refs/heads/main', commit)
wiki = root / 'source.wiki.git'
a.git(root, 'clone', '--mirror', str(repo), str(wiki))
wiki_point = a.capture(store, wiki, 'r-wiki', 'github:1:wiki', lfs)
a.capture(store, repo, 'r', 'https://github.com/lost/server', lfs, wiki=wiki_point)
shutil.rmtree(wiki)
shutil.rmtree(lfs)
shutil.rmtree(repo)
shutil.rmtree(store / '.projection')
(store / '.writer.lock').unlink()
"#])
        .env("PYTHONPATH", tools)
        .current_dir(root).output().unwrap();
    assert!(
        fixture.status.success(),
        "{}",
        String::from_utf8_lossy(&fixture.stderr)
    );
    let binary = root.join("gitpickle");
    std::fs::copy(env!("CARGO_BIN_EXE_gitpickle"), &binary).unwrap();
    let run = |options: &[&str]| {
        Command::new(&binary)
            .args(["restore", "--from-storage", "archive", "--repository", "r"])
            .args(options)
            .env("GIT_DIR", "/lost/server/database")
            .env("HTTPS_PROXY", "http://127.0.0.1:1")
            .current_dir(root)
            .output()
            .unwrap()
    };
    for options in [&["--inventory"][..], &["--dry-run"][..]] {
        let output = run(options);
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(String::from_utf8_lossy(&output.stdout).contains("r"));
    }
    let output = run(&["--destination", "recovered.git"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("\"git_integrity\": \"passed\""));
    let integrity = Command::new("git")
        .args(["-C", "recovered.git", "fsck", "--full", "--strict"])
        .current_dir(root)
        .output()
        .unwrap();
    assert!(integrity.status.success());
    let oid = std::fs::read_to_string(root.join("lfs-oid")).unwrap();
    let lfs_path = format!("lfs/objects/{}/{}/{}", &oid[..2], &oid[2..4], oid);
    for repository in ["recovered.git", "recovered.git/wiki.git"] {
        assert_eq!(
            std::fs::read(root.join(repository).join(&lfs_path)).unwrap(),
            b"CLI required LFS bytes"
        );
        assert!(
            Command::new("git")
                .args(["-C", repository, "fsck", "--full", "--strict"])
                .current_dir(root)
                .output()
                .unwrap()
                .status
                .success()
        );
    }
    assert!(
        String::from_utf8_lossy(&output.stdout)
            .contains("\"verified_components\": [\"git\", \"lfs\", \"wiki\"]")
    );
    assert!(!run(&["--destination", "recovered.git"]).status.success());
    assert!(!root.join("archive/.projection").exists());
}

#[test]
fn provider_recovery_uses_only_paginated_storage_reads() {
    let temporary = tempfile::Builder::new()
        .prefix(&std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "recovery-wire".into()))
        .tempdir()
        .unwrap();
    let tools = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tools");
    let output = Command::new("python3").args(["-B", "-c", r#"
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, unquote
from xml.sax.saxutils import escape
import os, shutil, subprocess, sys, threading
import gitpickle_archive as a
store = Path.cwd() / 'archive'
a.initialize(store, 'wire-recovery')
repo = Path.cwd() / 'source.git'
repo.mkdir()
a.git(repo, 'init', '--bare', '--template=', '--initial-branch=main')
tree = a.git(repo, 'mktree', data=b'').decode().strip()
commit = a.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit-tree', tree, data=b'wire history').decode().strip()
a.git(repo, 'update-ref', 'refs/heads/main', commit)
a.capture(store, repo, 'r', 'https://github.com/lost/original')
objects = {'private/' + str(p.relative_to(store)): p.read_bytes() for p in store.rglob('*') if p.is_file() and not any(part.startswith('.') for part in p.relative_to(store).parts)}
shutil.rmtree(store)
shutil.rmtree(repo)
requests = []
class StorageHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        requests.append(('GET', self.path))
        if 'list-type' in query:
            assert query['prefix'] == ['private/']
            index = int(query.get('continuation-token', ['0'])[0])
            keys = sorted(objects)[index:index+2]
            truncated = index + 2 < len(objects)
            body = ('<ListBucketResult><IsTruncated>' + str(truncated).lower() + '</IsTruncated>' + ''.join('<Contents><Key>' + escape(key) + '</Key></Contents>' for key in keys) + ('<NextContinuationToken>' + str(index+2) + '</NextContinuationToken>' if truncated else '') + '</ListBucketResult>').encode()
        else:
            key = unquote(url.path.removeprefix('/bucket/'))
            if key not in objects:
                self.send_error(404)
                return
            body = objects[key]
        self.send_response(200)
        self.send_header('ETag', '"opaque-not-a-digest"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
server = ThreadingHTTPServer(('127.0.0.1', 0), StorageHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    env = dict(os.environ, GITPICKLE_STORAGE_ACCESS_KEY='testkey', GITPICKLE_STORAGE_SECRET_KEY='testsecret', NO_PROXY='127.0.0.1', HTTPS_PROXY='http://127.0.0.1:1', GIT_DIR='/lost/database')
    for provider, region in [('r2', 'auto'), ('s3', 'us-east-1'), ('b2', 'us-west-004')]:
        destination = Path.cwd() / (provider + '-recovered.git')
        command = [sys.argv[1], 'restore', '--from-storage', '--provider', provider, '--endpoint', 'http://127.0.0.1:' + str(server.server_port), '--region', region, '--bucket', 'bucket', '--prefix', 'private/', '--destination', str(destination)]
        import json
        card_result = subprocess.run(command + ['--recovery-card', '--json'], env=env, capture_output=True, timeout=20)
        assert card_result.returncode == 0, card_result.stderr
        card = json.loads(card_result.stdout)['data']
        assert card['archive_id'] == 'wire-recovery'
        assert card['storage'] == dict(provider=provider, endpoint='http://127.0.0.1:' + str(server.server_port),
            region=region, bucket='bucket', prefix='private/')
        assert 'testsecret' not in card_result.stdout.decode()
        assert not destination.exists()
        result = subprocess.run(command + ['--archive-id', card['archive_id'], '--root-digest', card['root_digest']],
            env=env, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert a.git(destination, 'rev-parse', 'HEAD').decode().strip() == commit
    assert sum('list-type=' in path for _, path in requests) > 3
    assert all(method == 'GET' for method, _ in requests)
finally:
    server.shutdown()
    server.server_close()
    thread.join()
"#]).arg(env!("CARGO_BIN_EXE_gitpickle"))
        .env("PYTHONPATH", tools)
        .current_dir(temporary.path()).output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn installed_binary_reports_scoped_status_and_health_exits() {
    let temporary = tempfile::Builder::new()
        .prefix(&std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "status-cli".into()))
        .tempdir()
        .unwrap();
    let root = temporary.path();
    let tools = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tools");
    let fixture = Command::new("python3")
        .args([
            "-B",
            "-c",
            r#"
from pathlib import Path
import gitpickle_archive as a
root = Path.cwd()
store = root / 'archive'
a.initialize(store, 'status-cli')
repo = root / 'source.git'
repo.mkdir()
a.git(repo, 'init', '--bare', '--template=', '--initial-branch=main')
a.capture(store, repo, 'r', 'github:1')
"#,
        ])
        .env("PYTHONPATH", tools)
        .current_dir(root)
        .output()
        .unwrap();
    assert!(
        fixture.status.success(),
        "{}",
        String::from_utf8_lossy(&fixture.stderr)
    );
    let binary = root.join("gitpickle");
    std::fs::copy(env!("CARGO_BIN_EXE_gitpickle"), &binary).unwrap();
    let run = |args: &[&str]| {
        Command::new(&binary)
            .args(args)
            .env("GITPICKLE_ARCHIVE", root.join("archive"))
            .env_remove("PYTHONPATH")
            .current_dir(root)
            .output()
            .unwrap()
    };
    for args in [
        vec![],
        vec!["status"],
        vec!["status", "--check"],
        vec!["doctor"],
    ] {
        let output = run(&args);
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(String::from_utf8_lossy(&output.stdout).contains("1 current and verified"));
        assert!(output.stderr.is_empty());
    }
    let json = run(&["--json"]);
    assert!(String::from_utf8_lossy(&json.stdout).contains("\"schema_version\": 1"));
    assert!(String::from_utf8_lossy(&json.stdout).contains("\"repository_ids\": [\"r\"]"));
    let invalid = run(&["status", "--repository", "missing", "--json"]);
    assert_eq!(invalid.status.code(), Some(5));
    assert!(String::from_utf8_lossy(&invalid.stdout).contains("\"code\": \"not_found\""));
    assert!(invalid.stderr.is_empty());
    std::fs::remove_dir_all(root.join("archive/repos/r/checks")).unwrap();
    assert!(run(&["status"]).status.success());
    assert_eq!(run(&["status", "--check"]).status.code(), Some(6));
    assert_eq!(run(&["doctor", "--attention"]).status.code(), Some(6));
    let no_config = Command::new(&binary)
        .args(["--json"])
        .env_remove("GITPICKLE_ARCHIVE")
        .current_dir(root)
        .output()
        .unwrap();
    assert_eq!(no_config.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&no_config.stdout).contains("\"code\": \"configuration\""));
    let missing_runtime = Command::new(&binary)
        .args(["status", "--json"])
        .env("PATH", "/missing-gitpickle-runtime")
        .current_dir(root)
        .output()
        .unwrap();
    assert_eq!(missing_runtime.status.code(), Some(5));
    assert!(missing_runtime.stderr.is_empty());
    assert!(String::from_utf8_lossy(&missing_runtime.stdout).contains("operational_failure"));
}

#[test]
fn installed_binary_serves_authenticated_management() {
    let temporary = tempfile::Builder::new()
        .prefix(&std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "security-cli".into()))
        .tempdir()
        .unwrap();
    let tools = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tools");
    let output = Command::new("python3").args(["-B", "-c", r#"
import http.client, json, os, select, signal, subprocess, sys
from pathlib import Path
import gitpickle_archive as a
root = Path.cwd()
store = root / 'archive'
a.initialize(store, 'management-cli')
store.chmod(0o700)
source = root / 'source.git'
source.mkdir()
a.git(source, 'init', '--bare', '--template=', '--initial-branch=main')
for repository in ('github-123', 'explicit'):
    a.capture(store, source, repository, 'local-fixture')
sandbox = root / 'sandbox'
sandbox.mkdir(mode=0o700)
policy = root / 'policy.json'
policy.write_text(json.dumps({'revision': 1, 'grants': [
    {'actor': 'reader', 'permission': 'status', 'repositories': [], 'sources': ['github'], 'expires_at': 2**62},
    {'actor': 'reader', 'permission': 'status', 'repositories': ['explicit'], 'expires_at': 2**62}
], 'approvers': [], 'sandbox': str(sandbox)}))
policy.chmod(0o600)
binary = root / 'gitpickle'
import shutil
shutil.copy2(sys.argv[1], binary)
# A test process owns the child and terminates it in finally. Register recovery
# cleanup first when the surrounding Looper operation supplies a cleanup file.
pidfile = root / 'daemon.pid'
cleanup = os.environ.get('LOOPER_RESOURCE_CLEANUP_FILE')
if cleanup:
    import shlex
    with open(cleanup, 'a') as stream:
        stream.write('\n# Owned temporary management acceptance child\n')
        stream.write('python3 - ' + shlex.quote(str(pidfile)) + ' ' + shlex.quote(str(store)) + " <<'GITPICKLE_CLEANUP'\n")
        stream.write("import os, pathlib, signal, sys\np = pathlib.Path(sys.argv[1])\ntry:\n pid = int(p.read_text())\n expected = os.fsencode(sys.argv[2])\n command = pathlib.Path(f'/proc/{pid}/cmdline').read_bytes().split(bytes([0]))\n if expected in command:\n  os.kill(pid, signal.SIGTERM)\nexcept (OSError, ValueError):\n pass\nGITPICKLE_CLEANUP\n")
command = [str(binary), 'serve', '--state', str(store), '--policy', str(policy)]
env = dict(os.environ, GITPICKLE_MANAGEMENT_TOKENS=json.dumps({'t' * 40: {'actor': 'reader'}}))
child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
pidfile.write_text(str(child.pid))
try:
    assert select.select([child.stdout], [], [], 15)[0], 'startup timeout'
    line = child.stdout.readline()
    if os.geteuid() == 0:
        assert child.wait(timeout=5) == 2
    else:
        assert line, child.stderr.read()
        host, port = json.loads(line)['listen']
        assert host == '127.0.0.1' and port > 0
        for token, expected in [('wrong', 401), ('t' * 40, 403)]:
            connection = http.client.HTTPConnection(host, port, timeout=3)
            try:
                connection.request('POST', '/v1/management', json.dumps({'operation': 'status', 'arguments': {'repository': 'denied'}}),
                    {'Authorization': 'Bearer ' + token, 'X-GitPickle-Management-Version': '1', 'X-GitPickle-Result-Version': '1'})
                response = connection.getresponse()
                assert response.status == expected
                assert token.encode() not in response.read()
            finally:
                connection.close()
        for repository in ('github-123', 'explicit'):
            connection = http.client.HTTPConnection(host, port, timeout=3)
            try:
                connection.request('POST', '/v1/management', json.dumps({'operation': 'status', 'arguments': {'repository': repository}}),
                    {'Authorization': 'Bearer ' + 't' * 40, 'X-GitPickle-Management-Version': '1', 'X-GitPickle-Result-Version': '1'})
                response = connection.getresponse()
                body = response.read()
                assert response.status == 200, body
                assert json.loads(body)['result']['repository'] == repository
            finally:
                connection.close()
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=10) == 0
finally:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    child.stdout.close()
    child.stderr.close()
    pidfile.unlink(missing_ok=True)
"#]).arg(env!("CARGO_BIN_EXE_gitpickle"))
        .env("PYTHONPATH", tools)
        .current_dir(temporary.path()).output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn advanced_help_and_noninteractive_workflow_errors() {
    let output = cli(&["help", "--all"]);
    assert!(output.status.success());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.contains("--resume"));
    assert!(text.contains("--wait"));
    assert!(text.contains("NO_COLOR"));
    for command in ["protect", "backup", "sync", "restore"] {
        let output = Command::new(env!("CARGO_BIN_EXE_gitpickle"))
            .args([command, "--json"])
            .env_remove("GITPICKLE_SERVER")
            .stdin(std::process::Stdio::null())
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(2));
        let text = String::from_utf8(output.stdout).unwrap();
        assert_eq!(text.lines().count(), 1);
        assert!(text.contains("\"schema_version\": 1"));
        assert!(text.contains("configuration"));
        assert!(!output.stderr.is_empty());
    }
}

#[test]
fn installed_workflow_detaches_and_publishes_through_provider_ports() {
    let temporary = tempfile::Builder::new()
        .prefix(&std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "workflow-cli".into()))
        .tempdir()
        .unwrap();
    let tools = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tools");
    let output = Command::new("python3")
        .args(["-B", "-c", r#"
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from xml.sax.saxutils import escape
from test_gitpickle_workflow import WorkflowTests

binary = sys.argv[1]
fixture = WorkflowTests()
fixture.setUp()
child = None
try:
    fixture.apply(fixture.preview())
    fixture.release.clear()
    fixture.jobs.start()
    assert fixture.started.wait(5)
    env = dict(os.environ, GITPICKLE_MANAGEMENT_TOKEN=fixture.token, NO_COLOR='1')
    env.pop('PYTHONPATH', None)
    child = subprocess.Popen([binary, 'backup', '--repository', 'github-1', '--key', 'signal-detach',
        '--yes', '--json', '--wait', '--server', fixture.url], env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Read durable acceptance before sending an actual client signal.
    diagnostics = b''
    for _ in range(100):
        if select.select([child.stderr], [], [], 0.1)[0]:
            diagnostics += os.read(child.stderr.fileno(), 65536)
            if b'Durable job:' in diagnostics:
                break
    assert b'Durable job:' in diagnostics, diagnostics
    child.send_signal(signal.SIGINT)
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, stderr
    result = json.loads(stdout)
    assert result['data']['state'] == 'detached', result
    assert b'Server work continues' in stderr
    job = result['data']['jobs'][0]['id']
    assert not fixture.jobs.inspect(job)['cancel_requested']
    fixture.release.set()
    assert fixture.jobs.follow(job)['state'] == 'completed'
finally:
    fixture.release.set()
    if child is not None:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()
        child.stderr.close()
    fixture.doCleanups()

objects = {}
requests = []
class Storage(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def send(self, code, body=b''):
        self.send_response(code)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', '"opaque"')
        self.end_headers()
        self.wfile.write(body)
    def do_PUT(self):
        key = unquote(urlsplit(self.path).path.removeprefix('/bucket/'))
        requests.append(('PUT', key))
        body = self.rfile.read(int(self.headers['Content-Length']))
        if key.startswith('b2/'):
            assert self.headers.get('If-None-Match') is None
            assert key.endswith('/' + hashlib.sha256(body).hexdigest())
            assert key not in objects or objects[key] == body
        else:
            assert self.headers['If-None-Match'] == '*'
            if key in objects:
                return self.send(412)
        objects[key] = body
        self.send(200)
    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if 'list-type' in query:
            prefix = query['prefix'][0]
            body = ('<ListBucketResult><IsTruncated>false</IsTruncated>' + ''.join(
                '<Contents><Key>' + escape(k) + '</Key></Contents>' for k in sorted(objects) if k.startswith(prefix)) + '</ListBucketResult>').encode()
            return self.send(200, body)
        key = unquote(url.path.removeprefix('/bucket/'))
        requests.append(('GET', key))
        self.send(200, objects[key]) if key in objects else self.send(404)

with ThreadingHTTPServer(('127.0.0.1', 0), Storage) as server:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = Path('object')
        payload.write_bytes(b'independent archive bytes')
        for provider in ('r2', 's3', 'b2'):
            config = dict(provider=provider, endpoint='http://127.0.0.1:' + str(server.server_port),
                region='auto' if provider == 'r2' else 'us-east-1', bucket='bucket', prefix=provider + '/')
            env = dict(os.environ, GITPICKLE_STORAGE_CONFIG=json.dumps(config),
                GITPICKLE_STORAGE_ACCESS_KEY='testkey', GITPICKLE_STORAGE_SECRET_KEY='testsecret')
            command = [binary, '__archive-store', 'put', 'archive.json', str(payload)]
            for _ in range(2):
                result = subprocess.run(command, env=env, capture_output=True, timeout=15)
                assert result.returncode == 0, result.stderr
            physical = provider + '/archive.json'
            if provider == 'b2':
                physical = 'b2/.gitpickle-publication/v1/archive.json/' + hashlib.sha256(payload.read_bytes()).hexdigest()
            assert objects[physical] == payload.read_bytes()
            assert requests.count(('PUT', physical)) == 1
            caps = subprocess.run([binary, '__archive-store', 'capabilities'], env=env,
                capture_output=True, check=True, timeout=15)
            capabilities = json.loads(caps.stdout)
            assert capabilities['conditional_create'] == (provider != 'b2')
            if provider == 'b2':
                assert capabilities['archive_publication'] == 'content-addressed-v1'
            destination = Path('download-' + provider)
            subprocess.run([binary, '__archive-store', 'download', str(destination)], env=env,
                capture_output=True, check=True, timeout=15)
            assert (destination / 'archive.json').read_bytes() == payload.read_bytes()
            payload.write_bytes(b'changed bytes')
            result = subprocess.run(command, env=env, capture_output=True, timeout=15)
            assert result.returncode == 5
            payload.write_bytes(b'independent archive bytes')
        assert len(requests) >= 8
    finally:
        server.shutdown()
        thread.join()
"#])
        .arg(env!("CARGO_BIN_EXE_gitpickle"))
        .env("PYTHONPATH", tools)
        .current_dir(temporary.path())
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn installed_binary_generates_card_without_daemon_or_source_checkout() {
    let temporary = tempfile::Builder::new()
        .prefix(&std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "card-cli".into()))
        .tempdir()
        .unwrap();
    let binary = temporary.path().join("gitpickle");
    std::fs::copy(env!("CARGO_BIN_EXE_gitpickle"), &binary).unwrap();
    let archive = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../fixtures/archive-v1")
        .canonicalize()
        .unwrap();
    let output = Command::new(binary)
        .args(["restore", "--from-storage"])
        .arg(archive)
        .args(["--recovery-card", "--json"])
        .env_remove("PYTHONPATH")
        .env("GITPICKLE_MANAGEMENT_TOKEN", "SECRET_MANAGEMENT_TOKEN")
        .env("GITPICKLE_STORAGE_SECRET_KEY", "SECRET_STORAGE_KEY")
        .env("GITPICKLE_RECOVERY_LOCATION", "SECRET_INHERITED_LOCATION")
        .env("HTTPS_PROXY", "http://127.0.0.1:1")
        .current_dir(temporary.path())
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(!text.contains("SECRET"));
    assert!(text.contains("retained-v1-compatibility"));
    assert!(text.contains("\"format_version\": 1"));
    assert!(text.contains("\"required_application_keys\": []"));
    assert_eq!(text.lines().count(), 1);
    assert!(output.stderr.is_empty());
}

#[test]
fn installed_binary_guides_private_coolify_without_source_files() {
    let temporary = tempfile::Builder::new()
        .prefix(&std::env::var("LOOPER_RESOURCE_PREFIX").unwrap_or_else(|_| "setup-cli".into()))
        .tempdir()
        .unwrap();
    let binary = temporary.path().join("gitpickle");
    std::fs::copy(env!("CARGO_BIN_EXE_gitpickle"), &binary).unwrap();
    let image = format!("registry.example/gitpickle@sha256:{}", "a".repeat(64));
    let run = |resume: bool| {
        let mut command = Command::new(&binary);
        command.current_dir(temporary.path()).args([
            "setup", "--target", "coolify", "--output", "review", "--image", &image, "--port",
            "32123", "--json",
        ]);
        if resume {
            command.arg("--resume");
        }
        command.output().unwrap()
    };
    let first = run(false);
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    let result = String::from_utf8(first.stdout).unwrap();
    assert!(result.contains("\"schema_version\": 1"));
    assert!(result.contains("\"installed\": false"));
    let recipe = std::fs::read(temporary.path().join("review/compose.yaml")).unwrap();
    assert!(String::from_utf8_lossy(&recipe).contains("127.0.0.1:32123:8080"));
    let second = run(true);
    assert!(second.status.success());
    assert_eq!(
        recipe,
        std::fs::read(temporary.path().join("review/compose.yaml")).unwrap()
    );
    assert_eq!(std::fs::read_dir(temporary.path()).unwrap().count(), 2);
}

#[test]
fn terminal_interface_is_explicit_and_rejects_automation() {
    let output = cli(&["tui"]);
    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .contains("requires a terminal")
    );
    let output = cli(&["tui", "--json"]);
    assert_eq!(output.status.code(), Some(2));
    assert!(output.stderr.is_empty());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.contains("interactive_only"));
    assert!(!text.contains('\u{1b}'));
}
