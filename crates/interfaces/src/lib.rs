//! Command-line, terminal, management, Git gateway, and local agent interfaces.
//! Interfaces call shared application services.

/// The normal path keeps advanced controls one step away.
pub const HELP: &str = "GitPickle

Usage: gitpickle [COMMAND]

  protect github  Guide ongoing GitHub protection
  protect         Protect the GitHub remote of this checkout
  status          Show protection and effective scope (also the default)
  backup          Capture now; select --repository ID or --all
  sync            Refresh source state; select --repository ID or --all
  restore         Recover into a new directory
  doctor          Check configuration and recovery evidence

Next: gitpickle help --all
";

/// Advanced help documents automation and runtime credential channels.
pub const HELP_ALL: &str = "Usage: gitpickle COMMAND [OPTIONS]

Management client: --server http://127.0.0.1:PORT (or GITPICKLE_SERVER)
  GITPICKLE_MANAGEMENT_TOKEN supplies the runtime credential.
  Interactive credential entry is masked. Secrets have no command-line option.

protect [github] [--repository github-ID | --all] [--include-future]
  [--preview] [--resume REFERENCE] [--yes] [--wait] [--json]
backup|sync [--repository github-ID | --all] --key REQUEST_KEY
  [--preview] [--yes] [--wait] [--json]
jobs JOB_ID [--restore] [--wait] [--json]
status|doctor [--repository ID | --all] [--check] [--attention] [--json]
restore [ID] --destination NEW_DIRECTORY [--point IMMUTABLE_KEY] [--preview] [--yes] [--json]

Offline evidence: status|doctor --archive ARCHIVE [--repository ID | --all]
  [--freshness-seconds SECONDS] [--check] [--attention] [--json]
  GITPICKLE_ARCHIVE supplies the default archive directory.
Offline recovery: restore --from-storage ARCHIVE [--inventory | --dry-run]
  [--repository ID] [--point KEY] [--destination NEW_DIRECTORY]
  [--allow-incomplete] [--bundle] [--recovery-card]
Provider recovery: restore --from-storage --provider r2|s3|b2
  --endpoint URL --region REGION --bucket BUCKET --prefix PREFIX/
  Credentials use GITPICKLE_STORAGE_ACCESS_KEY and GITPICKLE_STORAGE_SECRET_KEY.

setup --target local|ssh|coolify --output DIRECTORY [--port PORT] [--json]
  [--binary VERIFIED_BINARY] [--host USER@HOST --known-hosts VERIFIED_FILE]
  [--ssh-port PORT] [--image NAME@sha256:DIGEST]
  Review first. Repeat with --resume --apply --yes to install a user service.
  Coolify generates a private recipe for manual deployment. No API provisioning.
  Setup never enrolls Tailscale or enables Funnel. See docs/setup.md.

tui [--server http://127.0.0.1:PORT]
  Optional keyboard interface. Requires a terminal; never runs for --json.
  See docs/tui.md for keys and plain CLI alternatives.
portfolio [--search TEXT] [--attention | --verified] [--json]
points|history --repository ID [--json]
  Browse authorized coverage, recovery points, and jobs.

hosting --repository ID [--json]
hosting --repository ID --action create
hosting --repository ID --action convert|resume --expected-head PUBLICATION
  Show the canonical Git remote URL and repository mode.
  Create hosted repositories or change modes with hosting_manage grants.

serve --state PRIVATE_ARCHIVE --policy PRIVATE_POLICY [--host 127.0.0.1]
  [--port 0] [--staging-bytes BYTES]
  [--enable-git --git-base-url https://HOST/git]
  Hosting requires git_read grants. HTTPS terminates at a trusted local proxy.
  See docs/git-hosting.md for authentication and compatibility.
  GITPICKLE_MANAGEMENT_TOKENS supplies runtime identities.
  GITPICKLE_GITHUB_TOKEN supplies the daemon credential for GitHub capture.
  GITPICKLE_STORAGE_CONFIG selects an existing R2 or S3 bucket and prefix.
  Without it, protection uses the filesystem archive adapter.
  B2 protection requires a conditional-write capability; recovery supports B2.
  See docs/cli-workflow.md for configuration and current limits.
  No command creates buckets. Bucket creation needs separate permission.

mcp --profile PROFILE [--config-preview EXISTING_JSON]
  Local stdio tools use a constrained runtime credential.
  GITPICKLE_PROFILES selects the nonsecret profile file.
  Configuration previews preserve existing settings and never write files.
  See docs/mcp.md for setup and approval requirements.

All prompts use the keyboard. Noninteractive calls never prompt.
Use --yes for approved writes. JSON is versioned; diagnostics use stderr.
Output uses plain text, supports NO_COLOR, and wraps to terminal width.
Times use UTC. Ctrl-C detaches clients; server work continues.
Cancellation is a separate operation. Retained points are never deleted.
Runtime requirements: Python 3.10 or later, Git; Git LFS for LFS capture.
";

pub mod machine;
pub mod negotiation;
