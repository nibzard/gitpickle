# GitHub discovery and protection

`tools/gitpickle_github.py` adds GitHub discovery to the archive reference service.
It uses the existing writer lock, immutable archive records, and durable job scheduler.
The Rust executable integration belongs to the interface and daemon tasks.
This implementation needs Python 3.10 or later and native Git.

## Authorization and scope

Supply a token through a runtime callback to `GitHub`.
The default endpoint lists repositories for the authenticated user.
Set `installation=True` for a GitHub App installation token.
The adapter follows authenticated pagination with 100 results per page.
It accepts next links only for the same HTTPS origin and endpoint.
It rejects redirects, pagination cycles, conflicting identities, and malformed responses.

The result means **authorized repositories found**.
It does not establish coverage of an entire account.
A partial page failure never replaces a completed inventory.
Rate limits retain a retry delay. Authentication failures mark sources unavailable.
Errors store stable codes, without response bodies or credential text.

Repository identity uses GitHub's numeric repository ID, independent of its name.
Internal IDs use `github-<numeric-id>`.
Names are aliases. Renames and transfers retain the internal ID and alias history.
Name reuse with a different GitHub ID creates a separate source.
Capture resolves the numeric ID before cloning and checks its alias again afterward.
A concurrent name change fails the attempt before publication.

The adapter uses the [GitHub repository API](https://docs.github.com/en/rest/repos/repos).
GitHub documents the [installation inventory endpoint](https://docs.github.com/en/rest/apps/installations#list-repositories-accessible-to-the-app-installation).

## Operations

`protect` persists an ongoing policy after confirmation of a concrete preview.
Select explicit numeric IDs, or select all currently authorized repositories.
Future inclusion requires a separate affirmative `include_future=True` in that preview.
Without it, daily discovery never expands the confirmed scope.
Newly discovered repositories remain protected if they later become unavailable.
Changing the policy can stop future scheduling without removing retained history.

`backup` accepts a capture now for explicit IDs or `all_protected=True`.
It returns durable jobs and does not change the policy.
The staging reservation is mandatory because source size is unknown.
Job arguments contain numeric IDs, without clone credentials or mutable names.

`sync` updates source tracking without publishing a recovery point.
`Protection.sync()` updates the inventory, aliases, and source availability.
A job with operation `sync` records source refs without calling capture.
Neither operation removes prior recovery points.

Disappearing repositories become **source unavailable**.
A successful empty inventory has that meaning, as does lost authentication.
An interrupted inventory retains the prior availability evidence and reports its error.
No discovery outcome establishes that a source was deleted.
No discovery outcome removes archive data.

## Server composition

The server owns both services. Clients do not own their lifetime.
For example, after obtaining a confirmed preview:

```python
with Jobs(archive, source_adapter=github) as jobs:
    protection = Protection(jobs, github)
    protection.protect(preview, preview['confirmation'])
    # Repeat these two calls from the server loop:
    protection.tick(staging_bytes=operator_staging_budget)
    jobs.tick()
```

Use `preview([123, 456])` for explicit scope.
Use `preview(all_authorized=True, include_future=True)` for confirmed future inclusion.
Review the preview before passing its confirmation digest to `protect`.
The service checks the selected inventory again when applying it.
This callback API assumes an authorized caller. Transport plan authorization belongs to T018.

The initial capture is due immediately. Later captures are due every six hours.
Discovery is due daily. Advanced callers can set both intervals in the confirmed preview.
Intervals must be positive integer seconds. Time values use Unix seconds in UTC.
A failed discovery pauses new scheduled acceptance until discovery succeeds.
Rate-limit retries respect the provider delay, with a minimum of 60 seconds.
Other discovery errors retry after five minutes.
These intervals do not guarantee capture freshness during an outage.

Protection records live at `protection/github/<digest>`.
Each versioned record contains the archive ID, predecessor, policy, aliases,
availability, discovery retry time, and next capture times.
Readers reject conflicting chains and digest corruption.
The service rebuilds from these records without SQLite.
A capture job is accepted before its next due time is checkpointed.
Replaying an interrupted scheduling pass reuses the same durable job ID.

The scheduler retains its concurrency, disk admission, fairness, and retry limits.
Run one protection inventory with its matching credential provider per archive.
The adapter supports github.com. Enterprise hosts require a separate adapter configuration.
HTTPS clone credentials exist only in the Git subprocess environment.
They never enter command arguments, Git configuration files, or archive records.
Temporary mirrors live under `.projection/github-staging/` and are removed after each attempt.
Interrupted staging is disposable and grants no recovery evidence.
Capture records separate LFS and wiki coverage. Discovery metadata alone grants no verification evidence.

## Verification

```sh
python3 -B -m unittest discover -s tools -p test_gitpickle_github.py -v
cargo xtask check
```

Tests use synthetic GitHub responses and native Git repositories.
They cover user and installation pagination across 404 repositories, scope confirmation,
partial authorization, page failures, rate limits, lost credentials, renames, transfers,
name reuse, schedule replay, bounded portfolio admission, and source loss.
The portfolio test completes 404 jobs within the expected number of scheduler rounds.
Each round checks the worker limit and waits for each worker with a timeout.
It then removes SQLite and checks all durable completion records after restart.
Separate tests reject identity changes during cloning and unavailable identities before cloning.
The capture test restores Git history using only archive data after removing the source.
These deterministic checks do not claim live GitHub or cloud provider evidence.
Generated test archives use ignored `fixtures/generated/github/`.
They use `LOOPER_RESOURCE_PREFIX` and create no external services.

## Required LFS and enabled wikis

Capture discovers Git Large File Storage (LFS) pointers throughout reachable Git history.
The GitHub adapter uses native `git lfs fetch --all` when pointers exist.
Install Git LFS on the capture host. Recovery needs only native Git and Python 3.
The adapter fixes the LFS endpoint to the selected GitHub repository and uses temporary object storage.
It validates every required SHA-256 digest and byte count before archive publication.
A missing Git LFS executable, unavailable object, or partial transfer retains Git progress with incomplete LFS coverage.
Run a new backup after content becomes available. Replaying the same job preserves its original outcome.

When GitHub reports `has_wiki: true`, capture also clones the canonical `.wiki.git` repository.
A wiki uses the stable repository ID with `-wiki` appended.
The main point links its exact wiki point. An unavailable wiki produces incomplete wiki coverage.
A disabled wiki has no required wiki component. Prior wiki points remain retained.
Unchanged captures reuse both points and upload zero Git or LFS payload bytes.
Submodule pointers remain intact. Dependent repositories need separate authorized protection.

Tests use local transport fixtures. They do not establish live GitHub or storage-provider conformance.
Run `python3 -B -m unittest discover -s tools -p test_gitpickle_components.py`.
