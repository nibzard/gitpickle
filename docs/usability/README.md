# Moderated usability acceptance

Status: prepared study protocol. No participant observations or acceptance results exist in this package.
T052 is complete in the supplied task context. T031 prepares this package during final validation.
T032 owns recruitment and study execution. T056 owns correction of measured failures and final release acceptance.

This protocol implements [section 8 and appendix D.3](../../gitpickle.md).
It covers onboarding comprehension and navigation through 404 repositories.
It does not replace provider, durability, performance, or fresh-machine deployment acceptance.

## Acceptance and scoring

Recruit at least five representative users who manage Git repositories and might operate self-hosted protection.
Include different levels of command-line, backup, and Git Large File Storage (LFS) experience.
Exclude implementation authors from the acceptance cohort. Record selection criteria and prior product exposure.
Use participant codes. Keep consent and contact details outside the evidence bundle.

For each participant, score these three outcomes separately and as a combined outcome:

| Outcome | Pass evidence |
| --- | --- |
| Unaided setup | Start with an unconfigured client. Establish protection through the product, without configuration-file editing or moderator instructions. Inspect the resulting coverage. |
| Incomplete-backup recognition | Identify the repository with missing required LFS. Explain that captured Git does not prove complete recovery. Identify retained data, current risk, and a next action. |
| Safe restore | Select a complete, verified point. Review its scope and destination. Restore into a previously nonexistent directory without assistance. Confirm integrity and preserve existing files. |

At least four participants must pass **all three outcomes**. Also report each individual outcome count.
Do not combine different participants' successes to meet the four-person threshold.
Every participant must distinguish configured, verified, and fresh without coaching.
For more than five participants, retain the section 8 threshold of four and require terminology comprehension from everyone.
Report the cohort size and all failures. Do not exclude unsuccessful participants after the session.
Missing evidence remains unresolved. A synthetic rehearsal cannot satisfy any participant criterion.

| Term | Moderator scoring reference; do not teach before scoring |
| --- | --- |
| Configured | A policy or registration exists. This alone proves neither captured data nor successful recovery. |
| Verified | Evidence checks the retained point and all required components. A successful job or Git-only capture is insufficient. |
| Fresh | Successful authorized source confirmation is within the configured threshold. An older unchanged point can still be fresh. |

A verified point can be stale. A configured repository can have no captured point.
A recent Git capture with missing LFS can be fresh but incomplete.
One sampled restore does not prove that the whole portfolio was restore-tested.

## Before each session

1. Record the candidate commit, binary checksum, platform, deployment profile, and document versions in `session-template.json`.
2. Obtain consent for notes and any recording. Explain withdrawal and the agreed retention period. Use only disposable study data.
3. Reset a private environment for this participant. Verify credentials, dependency availability, archive ownership, and a free listener port.
4. Save a recovery card separately. Provide authorized archive access and required keys through private runtime channels.
5. Rehearse every scenario from [scenarios.md](scenarios.md). Save fixture checks as synthetic evidence, separate from observations.
6. Confirm timestamps and thresholds. Reset fixtures before eight hours elapse. Verify the target restore directory does not exist.
7. Copy the evidence templates outside the checkout. Record environmental failures before participants start.

Use `LOOPER_RESOURCE_PREFIX` in private resource names during Looper execution.
Register idempotent, ownership-checked cleanup in `LOOPER_RESOURCE_CLEANUP_FILE` before creating external resources.
Preserve existing registrations and shared services. Follow the task's container-label requirements when using containers.
This package's rehearsal creates only local temporary files and a free loopback listener. It creates no external resources.
The fixture's context cleanup stops its listener, joins its thread, and removes its own temporary directory.
It does not change shared services, Git references, or repository settings.

## Moderator procedure

Plan 60 minutes: consent and background, 5; setup, 15; status, 10; restore, 10; navigation, 10; terminology and debrief, 10.
These limits standardize observation. They are study limits, not product performance requirements.
Record elapsed time, pauses, help use, errors, and assistance separately. Keep the same limits for every participant.

1. Say: “We are testing the product. Work as you normally do. Tell me what you expect and what you notice.”
2. Present scenario S1. Start the timer when its task card becomes visible. Do not reveal commands or the answer key.
3. Present S2 and S3. Capture the participant's first decision before asking neutral follow-up questions.
4. Present S4 after stopping only the owned study listener. Provide the recovery card and authorized recovery materials.
5. Present S5. Alternate interface order by participant code: odd codes start with the terminal interface; even codes start with the plain CLI.
6. Ask: “What do configured, verified, and fresh mean? Can one be true while another is false?” Record answers before debriefing.
7. Debrief and save observations. Independently check restored content and score each criterion against linked evidence.

CLI means command-line interface. TUI means terminal user interface.
For S5, repeat with the other interface and a different target repository. Record learning effects and interface order.
Navigation is diagnostic evidence. It does not replace the three section 8 outcomes or terminology requirement.

Participants can use built-in help and the ordinary product documents supplied to all participants.
Record each use; it is still unaided. Do not supply this protocol or moderator manifests to participants.
A neutral “What are you thinking?” prompt is allowed. Record it with its timestamp.
A command hint, pointing to a control, explaining terminology, or correcting a choice counts as assistance.
If a participant requests help, record the request and allow them to continue unaided or stop.
After scoring the unaided attempt, provide help if needed to explore the remaining workflow.
Mark later work as assisted. Do not change the initial score.
Stop immediately if scope includes real data or unexpected destructive changes.

If setup requires editing a configuration file, score setup as failed even when documented.
The current [setup guide](../setup.md) includes manual runtime configuration. This is a known study risk, not an observed user failure.
Do not preconfigure those steps and call the result successful setup.
An operator can prepare dependencies and disposable accounts. Record each prepared item and its boundary.
After the setup attempt, switch to the seeded environment for independent status and restore measurements.
This prevents a setup failure from hiding other usability problems.

## Evidence and decision

Copy [session-template.json](session-template.json) once per participant.
Use [observation-template.csv](observation-template.csv) for timestamped events and exact quotes.
Copy [report-template.md](report-template.md) for the aggregate decision.
Keep recordings and runtime secrets private. Redact credentials before sharing logs.
Retain original private evidence under the agreed access and retention rules.
Generated study evidence is ignored under `tests/results/usability/`; independent evidence storage is preferred.

Use `null` for unmeasured values. Never prefill passes, times, quotes, or findings.
Link each score to an event, terminal capture, or integrity check with an exact location.
Record participant reports separately from moderator interpretations and measured product state.
Classify interruptions as product failure, environment failure, withdrawal, or timeout, with evidence.
Retain interrupted sessions; document any additional recruitment without replacing inconvenient results.

The reviewer checks the combined four-person threshold and universal terminology comprehension.
If either fails, acceptance fails. Missing sessions or evidence leave acceptance unresolved.
Link measured defects to correction work in T056. Repeat affected scenarios after correction with candidate-bound evidence.
Retain the original failures and disclose prior exposure in any repeat cohort.
No study receipt is generated by this preparation task. Real observations remain the operator prerequisite in T032.

## Preparation verification

On 15 October 2026, both fixture modes passed their synthetic checks.
The portfolio check exercised complete-data restore, integrity, existing-destination rejection, and recovery after stopping its listener.
The existing workflow, terminal-interface, and status suites passed 13, 10, and 19 tests respectively.
A content-hash comparison confirmed that verification left tracked and unignored project files unchanged.
The generated report is `tests/results/usability/preparation-check.json`, an ignored local artifact.
These checks establish preparation behavior only. They contain no participant observations.

To repeat the checks, run both fixture commands in [scenarios.md](scenarios.md), then run:

```sh
python3 -B -m unittest discover -s tools -p 'test_gitpickle_workflow.py'
python3 -B -m unittest discover -s tools -p 'test_gitpickle_tui.py'
python3 -B -m unittest discover -s tools -p 'test_gitpickle_status.py'
```
