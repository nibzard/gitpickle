# Optional terminal interface

The terminal user interface (TUI) is optional.
The command-line interface (CLI) provides the same operations.

Run `gitpickle tui --server http://127.0.0.1:PORT` in a terminal.
Use `GITPICKLE_MANAGEMENT_TOKEN` or enter the masked management credential.
The server uses the same identity, grants, and application services as the CLI.
Agent identities cannot use operator workflows or approve plans.

The interface requires Python curses. The standard Linux Python package usually includes it.
No additional Rust dependency is required. The executable embeds the client module.

The interface opens only with `tui`. Noninteractive input or output returns an error.
`tui --json` returns a versioned `interactive_only` error without importing curses.
Model Context Protocol (MCP) stdio and ordinary JSON commands never start the interface.

## Keys

| Key | Action |
| --- | --- |
| `/` | Edit a search for a repository ID or alias. Enter submits. Escape cancels. |
| `f` | Cycle all, attention, and verified coverage filters. |
| Arrows or `j` and `k` | Move through the portfolio or scroll a detail view. |
| Enter | Open the selected repository. |
| `v` | Open recovery points. Use `n` and `p` to select a point. |
| `h` | Open the repository's capture, sync, and authorized restore jobs. |
| `b` or `s` | Review a backup or sync request for the selected repository. |
| `o` | Enter a new restore directory and review the restore plan. |
| Page Up or Page Down | Scroll a detail or review view. |
| `y` | Apply the reviewed operation after you reach the end of the plan. |
| `r` | Refresh the current portfolio, details, points, or jobs view. |
| Escape | Cancel the review and return to the portfolio. |
| `?` | Open help. |
| `q` or Ctrl-C | Exit. Accepted server jobs continue. |

Details show backup coverage separately from hosting mode and the canonical remote URL.
If your identity has no `git_read` grant, enabled hosting details show as unavailable.
Coverage uses the application calculation. A completed job alone does not prove coverage.
Repository text cannot emit terminal control sequences. Non-ASCII text uses visible escapes.
Output uses status words and no colors, including when `NO_COLOR` is set.
The layout supports terminals as small as 20 columns and six rows.
Smaller terminals show a resize message and accept `q` to exit.
Long details and immutable point keys wrap. Use scroll keys to read the full value.

Restore defaults to the newest complete, verified point.
In the points view, restore uses the selected point.
Incomplete points remain inspectable. Managed restore rejects them.
Use the explicit offline recovery CLI when an incomplete recovery is required.
A review shows the scope, destination, selected point, impact, expiry, and required authority.
The application binds approvals to the plan and checks authorization again before execution.
A stale or revoked plan fails. An error never grants authority.

## Plain CLI alternatives

| Terminal action | CLI command |
| --- | --- |
| Search and filter | `gitpickle portfolio --search alice/project --attention` |
| Coverage details | `gitpickle status --repository github-123` |
| Hosting mode and URL | `gitpickle hosting --repository github-123` |
| Recovery points | `gitpickle points --repository github-123` |
| Jobs | `gitpickle history --repository github-123` |
| Follow a job | `gitpickle jobs JOB_ID --wait` |
| Review a capture | `gitpickle backup --repository github-123 --key REQUEST --preview` |
| Review source refresh | `gitpickle sync --repository github-123 --key REQUEST --preview` |
| Review historical restore | `gitpickle restore github-123 --point KEY --destination NEW_DIRECTORY --preview` |

Remove `--preview` to review and approve an operation interactively.
Use `portfolio --verified` to show only current, verified repositories.
Automation uses `--yes` with explicit arguments. Add `--json` for versioned results.
For restore job inspection, add `--restore` to `jobs`.
`history` lists capture and sync jobs, plus your authorized restore jobs.
Accepted restore IDs also remain available in the result view and through `jobs --restore`.

## Local acceptance and measurements

Run the navigation and authorization tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_gitpickle_tui.py'
cargo test --locked -p gitpickle --test cli_contract
```

Run the bounded benchmark:

```sh
cargo build --locked -p gitpickle
PYTHONDONTWRITEBYTECODE=1 python3 tools/gitpickle_tui_bench.py
```

The benchmark creates 404 registered repositories in an isolated archive with no captured payload.
It uses the real authenticated loopback service and executable in an 80-column pseudo-terminal.
It records startup, the 95th percentile (p95) of search/filter and paint latency, and peak client memory.
It also records central processing unit (CPU) time.
It also records refresh output for 100 navigation keys and an idle CPU sample.
The report records hardware and measurement limits under ignored `perf/results/tui/`.
The first invocation does not imply an empty operating system cache.
These local measurements do not establish a budget for the final reference runner.

A local run on 15 October 2026 used Linux 6.8, Python 3.12.3, and eight logical CPUs.
The CPU model was Intel Core i9-12900HK. The executable used the debug profile.
This development run did not reserve exclusive CPU access.

| Measurement | Result |
| --- | --- |
| First invocation to useful paint | 163 ms |
| Warm paint p95, five invocations | 164 ms |
| Portfolio refresh through the service | 54.5 ms |
| Search/filter p95, 1,000 queries | 0.073 ms |
| Visible content layout p95 | 0.045 ms |
| Peak client resident memory | 30.75 MiB |
| CPU time for 100 navigation keys | 0.03–0.04 seconds |
| Terminal output for 100 navigation keys | 7,193–7,254 bytes |

Each 0.25-second idle sample recorded zero CPU ticks at the process clock resolution.
Memory values come from Linux `VmHWM` and `VmRSS`. The daemon is excluded.
The fixture contains registered repositories without captured payloads.
These results measure navigation over the inventory, not archive transfer performance.
