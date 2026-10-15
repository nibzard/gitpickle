# Scenario cards and reproducible preparation

The participant text below is the only task text to show during each scenario.
Expected states and commands are moderator references. They are not instructions for participants.
Use the actual packaged candidate for scored sessions. Record every fixture substitution.

## Local synthetic rehearsal

Run from the repository root with Python 3.11 or later and native Git installed.
The rehearsal reuses the workflow acceptance fixture and the real authenticated application service.
Its source adapter is synthetic. Storage is local. Tokens are disposable fixture values.
It does not exercise GitHub authorization, cloud access, actual installation, or clean-machine recovery.
It cannot establish real-user acceptance. It must not be used to claim successful setup.

1. Set `LOOPER_RESOURCE_PREFIX` to a private study identifier, unless Looper already supplies it.
2. Run `python3 -B tools/gitpickle_usability_fixture.py --mode onboarding --check`.
3. Run `python3 -B tools/gitpickle_usability_fixture.py --mode portfolio --check`.
4. Start a fresh fixture without `--check` for a hands-on rehearsal. Keep that terminal open.
5. In another terminal, source the printed `client.env` path. Read the private `moderator.json` in the moderator terminal.
6. Use the candidate `gitpickle` executable in the client terminal. Type `offline` in the moderator terminal for S4.
7. Type `exit` in the moderator terminal to stop the service and remove this fixture. Copy required rehearsal logs before exit.

Each invocation resets private state under ignored `fixtures/generated/workflow/` using the resource prefix.
The listener binds a free loopback port. The client environment selects that port.
The portfolio contains 404 registered repositories; only three have captured payloads.
Repository 1 has complete verified data. Repository 2 has a real committed LFS pointer without its payload.
Repository 3 has a verified capture timestamp nine hours in the past. Other repositories have no captures.
All wikis are disabled. This fixture does not test enabled wiki coverage.
Do not run capture or sync commands during seeded status observations: the synthetic adapter shares one source checkout.
Start a new fixture after any mutation. Recheck the expected state before the next participant.

## S1: Partial authorization and unaided setup

Participant card: “Protect the repositories this account permits. Tell me what is protected and whether the whole account is covered.”

Prepare a fresh client and disposable installation target with no product configuration.
Use an account with three known study repositories. Authorize the product for exactly two.
Give participants the ordinary access materials and installation instructions, without commands chosen by the moderator.
Record the known inventory separately from what the credential reveals. Never infer hidden repositories from discovery alone.
Allow the participant to select the execution profile and existing storage. Record their choices and review of scope.
Use a new authorized archive prefix. Bucket creation requires separate permission; no account-wide credentials are needed.

Expected state: discovery says two authorized repositories. It does not claim three are protected.
The policy can be configured while captures remain pending or incomplete.
Measure setup time, configuration-file edits, help use, scope interpretation, review decisions, and assistance.
The onboarding rehearsal exposes two repositories from a declared three-repository account.
Its `protect github --all --preview` checks scope and probes only; it is not an installation study.

## S2: Required LFS is missing

Participant card: “Is project-2 completely recoverable? Explain what remains available, what is at risk, and what you would do next.”

Prepare a repository with a committed LFS pointer and an unavailable required payload.
Capture through the normal service. Keep its Git data and incomplete point intact.
For the rehearsal, use `github-2`, alias `alice/project-2`.
Verify `status --repository github-2` and `points --repository github-2` before the session.

Expected state: Git remains captured; required LFS coverage is incomplete. Overall coverage cannot be verified.
The participant must identify the missing payload and avoid describing Git-only progress as complete protection.
Record the first-screen judgment, time to recognition, exact explanation, and proposed next action.
A safe answer can propose restoring source access or retrying after the required content becomes available.
Do not repair the LFS fixture until scoring ends.

## S3: Restore to a new destination

Participant card: “Recover project-1 into a new folder. Keep the existing work unchanged and check the recovered data.”

Prepare a complete verified point for `github-1`. Record exact refs and the committed file's checksum.
In the rehearsal, `refs/heads/main:committed` contains the four bytes `data`.
The manifest names a nonexistent directory inside the private restore sandbox.
Create a separate existing folder with a sentinel file. Record its checksum before the attempt.
Do not put the sentinel inside the intended destination.

Expected state: the review selects the complete point and new destination, with no overwrites.
The participant approves that exact plan. Changing the destination requires a new review.
Inspect completion, run `git -C DESTINATION fsck --full`, compare refs and committed bytes, and recheck the sentinel.
Use `git show refs/heads/main:committed` for a bare restore rather than assuming a working checkout exists.
Record destination existence before the operation, selected point, approval, completion, integrity result, and assistance.
An existing destination must be rejected safely. Do not use a real working directory to test rejection.

## S4: Offline server and archive-only restore

Participant card: “The protection server is unavailable. Recover project-1 into another new folder using the recovery materials.”

Use a separate nonexistent destination. Keep archive access and required keys available independently of the daemon.
Stop only the owned study listener. Confirm a management status request fails.
Do not delete the archive. Do not restart the server or provide GitHub credentials during the attempt.
In a real session, block GitHub on the isolated recovery machine and retain the blocking evidence.
For the local rehearsal, type `offline` and retain the fixture terminal until recovery completes.

Moderator command reference for the local archive:

```sh
gitpickle restore --from-storage "$STUDY_ARCHIVE" --repository github-1 \
  --destination "${STUDY_DESTINATION}-offline" --json
```

Expected state: ordinary restore offers the storage recovery path. The selected data passes integrity checks without the server.
Record offline detection, recovery-path discovery, key handling, destination review, and integrity results.
Local filesystem rehearsal is not evidence of cloud, encrypted-key, or clean-machine recovery.
Score S4 separately as a diagnostic scenario. It does not excuse a failed unaided S3 restore.

## S5: Portfolio navigation and evidence comprehension

Participant card: “Find project-2 and explain its coverage. Find project-3 and explain whether its recovery point is current.
Then inspect project-404. Show recovery points and jobs without starting a write.”

Prepare exactly 404 registered repositories. Keep hosting mode separate from protection coverage.
Use `gitpickle tui` for the terminal interface and `gitpickle portfolio`, `status`, `points`, and `history` for the plain alternative.
Do not show these command names until unaided observation ends; built-in help remains available.
For the second interface, use project-1 as the complete target and project-403 as the configured-only target.
Keep project-3 as the stale comparison. Record the repeated exposure.

Expected state: search, filter, details, points, and history remain scoped to the selected repository.
Inspection performs no write. Permission-gated operations require an explicit review and approval.
Project-3 remains verified but stale under the eight-hour default. Project-404 is configured without captured data.
Measure time to find each target, navigation errors, mistaken writes, help use, and explanation accuracy.
Use keyboard-only navigation. Repeat a narrow-terminal check and `NO_COLOR` observation; record actual dimensions.
Do not infer comprehension or accessibility from scripted keyboard tests alone.
