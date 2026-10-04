# ADR 0002 — Release publishing waits for the UI gate

- **Repo:** r5-ha-addon
- **Status:** Accepted (2026-10-04). I chose Option A of the RFC on the day it was raised.
- **Context:** r5's `release.yaml` tagged v1.8.15 two minutes after the merge while the same commit's UI-gate render was still running for twelve more, and the UI gate is not a required PR check. a290-ha-addon fixed the same thing in its ADR 0005 after v1.28.11; r5 follows a290 and is never ahead of it. Proposed as [the RFC "r5-ha-addon: Release waits for the UI gate"](https://claude.ai/artifact/KMQvoeWTkcv6GUxsoj5pt5), which carries the options and the verification plan.
- **North star:** a release cannot publish, and a PR cannot merge, ahead of a UI-gate verdict on that exact commit, whether or not the UI gate was relevant to what changed.

<!-- Format: claude-config docs/adr/0000-template.md, referenced at source rather than copied.
The Decision table is the plan and is kept current; the rest is the dated record. -->

## Decision

The same design as a290-ha-addon/ADR 0005. `ui-tests.yaml` reports a verdict on every push and every PR, relevant or not. `release.yaml` waits for that verdict, and for CI's, before it creates the git tag and the GitHub Release. Once that holds on live PRs, the two render legs become required checks. The workflow files are taken from a290's current `main`, not from its original PR, because a290 fixed a bug in that PR afterwards (row 1).

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `ui-tests.yaml`: remove the trigger-level `paths:`; a `changes` job (`dorny/paths-filter`, the same patterns) decides relevance; each render **step** is gated on it, never the job, so the matrix still expands into the two named checks | r5 | Open | |
| 2 | `release.yaml`: a `validated` job waits for the render legs and CI's jobs on `github.sha`, and `tag` needs it. It runs only on a push that publishes | r5 | Open | |
| 3 | `refresh-screenshots.yaml`: a `has-render` check, so a render that was skipped does not try to download an artifact that was never produced | r5 | Open | |
| 4 | Add `Dashboard responsive render (mobile matrix, stable)` and `(minimum)` to `main`'s required checks, after rows 1 to 3 hold on a live PR. I approve this step; it changes repo settings | r5 | Open | |
| 5 | Verify live, in the order of the RFC's verification plan: names first with no setting change, then the required checks and a second docs-only PR that must be `CLEAN`, then the `has-render` guard after merge, then the `validated` patterns against a past `main` commit including a name that must time out | r5 | Open | |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context (2026-10-04)

On the v1.8.15 release commit `d31a4e5`, Release and UI Tests started together at 08:38:05 UTC. The tag and the GitHub Release were published at 08:39:43 and the Release run finished at 08:39:46. UI Tests on the same commit finished at 08:51:53. The gate was green, so nothing shipped broken, but a red render would have arrived after the release was public. The image is published from the PR before merge, so the tag is the "this version is out" signal and not image availability.

`ui-tests.yaml` is path-filtered, so it does not start at all for most pushes. A check that a required rule names but that never starts stays "Expected" for ever and blocks the merge button, so it cannot be required as it stands.

The `validated` job waits for CI by name, so its pattern names r5's own parity job, `Parity with a290`. r5's minimum leg runs for about sixteen minutes, so a release waits about that long.

## Alternatives

Copied from the RFC at decision time.

| Option | What it does | Cost | Risk |
| --- | --- | --- | --- |
| **A. Port in full (chosen)** | All five rows of a290's ADR 0005, then the two render legs become required | One PR over three workflow files; every PR gets a fast no-op `UI Tests`; releases wait about sixteen minutes | The "skipped counts as fine" rule lives in three files and must agree in all three; the matrix check-name bug returns if the `if:` goes back on the job |
| B. Release wait only | Add `validated` to `release.yaml`; leave `ui-tests.yaml` path-filtered and not required | Smallest diff, one file | `validated` waits on a check that may never exist, so it needs a guessed grace period, a timing heuristic. PRs can still merge on a red render |
| C. Do nothing | Accept that the tag can precede the gate | Zero | The v1.8.15 ordering repeats on every release, and the next red one ships first |

## Consequences

**Accepted.**
- `ui-tests.yaml` runs, as a fast no-op, on every PR and push, in exchange for a check that can be required.
- `refresh-screenshots.yaml` fires on every PR too and needs its own render-skipped guard.
- A release takes about sixteen minutes longer to tag when a render is relevant. The image is already published, so this delays the tag and the GitHub Release, not availability.

**Watch.**
- Three places encode "skipped counts as fine": the `changes` job, the `validated` wait, and the `has-render` guard. A change to one without the others is the drift this ADR exists to prevent, and nothing checks them against each other beyond the live tests in row 5.
- The `validated` wait waiting and then gating a real version bump cannot be shown before the next requested release, and I will not cut one to find out. a290's v1.28.12 ran the same action at the same pin and passed. Neither repo has seen the failing case, CI or UI red after merge.

## References

- [RFC: r5-ha-addon: Release waits for the UI gate](https://claude.ai/artifact/KMQvoeWTkcv6GUxsoj5pt5)
- a290-ha-addon/ADR 0005, the design this ports
- [ADR 0001](0001-release-on-request.md), which this builds on: a release happens when I ask for one
