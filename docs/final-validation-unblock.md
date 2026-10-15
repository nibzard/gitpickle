# Unblock the final-validation tasks

This guide is for the project owner. It lists every blocked Looper task and
gives steps to unblock each one. Looper is the task runner that drives the
coding workers. It is idle because the remaining work needs owner action.

Status on 15 October 2026, 22:45: 49 of 58 tasks done. Five tasks are
blocked on owner action. Four tasks wait behind them.

## Current blockers

| Task | Title | Owner action | Unblocks |
| --- | --- | --- | --- |
| T058 | Select the project distribution license | Pick a license and record it | Release notices |
| T055 | Validate fresh VM deployments on pve2 | Fix host access, then run acceptance | T051, T037, T056 |
| T053 | Provide S3 and B2 accounts | Create disposable storage access | T054, T037, T056 |
| T032 | Complete usability validation | Run the moderated study | T033, T056 |
| T033 | Resolve naming and brand clearance | Get a written naming determination | T056 |

Work in this order:

1. T058. It has no dependencies and takes minutes.
2. T055 and T053. They open the two technical paths.
3. Coding tasks resume by themselves: T051 after T055, T054 after T053,
   and T037 after both.
4. T032. Run the study while the coding tasks finish.
5. T033, then T056. They close the project.

## How to mark a task done

Each blocked task carries the `looper:operator` tag. Looper will not
schedule it. After you complete the action, close the task yourself.

1. Complete the action described in the task section below.
2. Record the evidence. Use the file named in the task, or add it to the
   task `details` field. Never put credentials in files.
3. Open `to-do.json` in the repository root. Find the task entry.
4. Set `"status"` to `done`. Set `"blockers"` to `[]`. Update `updated_at`.
5. Run `looper status`. Confirm the counters changed.
6. If the controller stays idle after one minute, run `looper stop` and
   then `looper start`. Retained work resumes.

## T058: select the project distribution license

**Blocker.** No decision was recorded. The repository has no `LICENSE`
file. `Cargo.toml` declares no license. Generated setup notices disclose
this gap.

**Steps:**

1. Choose the license. Common choices for a self-hosted open-source
   application: MIT (simple permissions), Apache-2.0 (patent grant), or
   AGPL-3.0 (strong network copyleft). The choice is yours.
2. Write the full license text to `LICENSE` in the repository root.
3. Add the license identifier to `Cargo.toml` under `[workspace.package]`
   (line 12), for example `license = "Apache-2.0"`.
4. Record the decision and its date in `docs/release/reviews.md`.
5. Mark T058 done as described above.

## T055: validate fresh VM deployments on pve2

**Blocker.** Two access faults stop all fresh-host validation. Both were
reproduced on 15 October 2026:

- Secure Shell (SSH) fails. `ssh pve2` uses `~/.ssh/id_ed25519_agentlab`, and both
  `agent@pve2` and `root@pve2` reject that key with
  `Permission denied (publickey)`.
- Transport Layer Security (TLS) name mismatch. The AgentLab endpoint
  answers at `pve.agentlab.local`, but its certificate covers only `pve2`.
- Also note: the local AgentLab socket `/run/agentlab/agentlabd.sock` is
  root-owned. Run local AgentLab commands with `sudo`, or reach the
  remote daemon with `--endpoint`.

**Part A: fix SSH.**

1. Print the public key: `cat ~/.ssh/id_ed25519_agentlab.pub`.
2. On pve2, through the Proxmox web console, append that key line to
   `/root/.ssh/authorized_keys` and to the `agent` account's
   `authorized_keys` file.
3. Test both logins: `ssh root@pve2 echo ok` and
   `ssh agent@pve2 echo ok`.

**Part B: fix the TLS name.**

4. Pick one fix:
   - Point AgentLab at a name the certificate covers. Add a hosts entry
     that maps `pve2` to the daemon address, then call
     `agentlab --endpoint https://pve2 --token <token> status`.
   - Or reissue the daemon certificate with `pve.agentlab.local` as a
     subject alternative name (SAN). Re-run
     `agentlab bootstrap --host pve2` with the corrected names.
5. Confirm access: `agentlab status` must return the daemon state.

**Part C: run the acceptance.** Follow `docs/deployment-acceptance.md`
from step 1 ("Provision a fresh, isolated `pve2` virtual machine").
Provision one virtual machine (VM) per profile, run the binary with
systemd and the Coolify container profile, and record readiness, HTTPS
latency, and cold and warm recovery evidence separately. Do not use this
development machine as a substitute. Register owned cleanup before you
provision, and preserve unrelated hosts.

Then record the results in `docs/deployment-acceptance.md` and mark T055
done. T051 needs the pinned runner hardware you allocate here.

## T053: provide S3 and B2 accounts

**Blocker.** Amazon S3 and Backblaze B2 test access was never provided.
R2 access for development exists and is handled separately by T004.

**Steps:**

1. Create one disposable account for S3 and one for B2.
2. In each account, create one dedicated test bucket.
3. Create scoped credentials. For S3, restrict an identity policy to the
   one bucket. For B2, create an application key limited to that bucket.
   Allow read, write, list, and delete on a test prefix only.
4. Build the command-line interface (CLI): `cargo build --release`. No
   binary exists yet.
5. Enter the credentials through the guided prompts at runtime:
   `./target/release/gitpickle setup`. Choose **local**. The prompts mask
   secrets. Credentials must travel only through this runtime channel.
6. Confirm the test scope. A conformance run must write and delete only
   under its prefix, and cleanup must remove only resources it owns.
7. Record the non-secret evidence in the task `details`: provider,
   endpoint, region, bucket, allowed prefix, and credential scope. Mark
   T053 done.

T054 then becomes ready. Workers run the conformance and recovery
verification against these accounts.

## T032: complete usability validation

**Blocker.** No participant observations exist. The protocol is ready in
`docs/usability/`.

**Steps:**

1. Build the release binary with `cargo build --release`.
2. Read `docs/usability/README.md` and `docs/usability/scenarios.md`.
   Recruit at least five representative users who manage Git
   repositories. Exclude the implementation authors. Record selection
   criteria and prior exposure.
3. Use participant codes. Keep consent and contact details outside the
   evidence bundle.
4. Run one moderated session per participant. Score three outcomes
   separately: unaided setup, incomplete-backup recognition, and safe
   restore. Use `observation-template.csv` and `session-template.json`.
5. Compile the report with `report-template.md`. Report every failure.
   Do not drop unsuccessful participants.
6. Apply the threshold: at least four of at least five participants must
   pass all three outcomes. Every participant must distinguish
   **configured**, **verified**, and **fresh** without coaching.
   Git Large File Storage (LFS) gaps must be recognized as incomplete
   coverage.
7. Save the report under `docs/usability/` and mark T032 done.

If criteria fail, keep the failures as unresolved evidence for the
release gate. T056 owns the corrections.

## T033: resolve naming and brand clearance

**Blocker.** Deferred by owner decision to the last final-validation
stage. Development continues under the working name.

**Steps:**

1. Confirm the dependencies are done: T032, T037, T051, T054, and T055.
2. Request a written determination for public use of the GitPickle name
   from Software Freedom Conservancy.
3. Complete the domain, package, and trademark checks listed in the
   determination request.
4. If permission is not viable, evaluate the RepoPickle fallback and
   record that decision.
5. Create `docs/brand-clearance.md` with the determination, the checks,
   and the date. Keep public branding on hold until this file exists.
6. Apply any rename across the README, docs, and package names.
7. Mark T033 done.

## Also open: one code defect

The final review confirmed a real defect with executable evidence:
repositories added after setup fail capture authorization when
`include_future` is enabled. `tools/gitpickle_setup.py:172-178` grants
only the initial repository identifiers. The finding is recorded on T015,
which is already done. No open task carries the fix.

Fix it before T032, because study scenarios use partial authorization.
Either fix `tools/gitpickle_setup.py` directly, or add a small task entry
to `to-do.json` so Looper schedules the repair.

## Note on the running daemon

The Looper binary at `~/.local/bin/looper` was replaced at 22:02 on
15 October 2026. The running daemon and terminal interface still use the
older build. Run `looper stop` and `looper start` to align them. Retained
work resumes, and no task state is lost.
