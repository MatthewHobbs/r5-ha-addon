# ADR 0001 — Releases are batched and cut on request

- **Repo:** r5-ha-addon
- **Status:** Accepted (2026-10-03). I delegated the acceptance, and the other decisions in this port, to the session driving it, having accepted a290-ha-addon/ADR 0006 myself; this ADR states the same rule for r5.
- **Context:** `release.yaml` publishes a release for any merge that moves `version` in `renault_5/config.yaml`, and the rule in `CLAUDE.md` was that every user-facing change moves it. I decided in a290-ha-addon/ADR 0006 that I want releases batched until I ask for one; r5 follows a290 and is never ahead of it (that ADR, row 5). a290-ha-addon/ADR 0006 holds the reasoning, the alternatives and five review rounds on the guard; I do not copy them here.
- **North star:** a release happens when I ask for one, and in no other way: no merge can publish a version, and the version on `main` always has its image.

<!-- Format: claude-config docs/adr/0000-template.md, referenced at source rather than copied.
The Decision table is the plan and is kept current; the rest is the dated record. -->

## Decision

The same rule as a290-ha-addon/ADR 0006. Ordinary PRs stop moving `version`: a user-facing change adds its entry under `## Unreleased` in `renault_5/CHANGELOG.md` and leaves `config.yaml` alone. A release is its own PR, opened only when I ask: it renames `## Unreleased` to `## <version>`, moves `version` to match, and changes nothing else. `scripts/prepare_release.py` (`just release <version>`) makes that edit and `scripts/docs_sync_check.py` refuses a PR that moves `version` without being exactly that edit, with no label to waive it. Both scripts are copied verbatim from a290-ha-addon, where they are shared files and change in both repos together.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Take a290's guard and script verbatim, the `justfile` recipes and the `just ci` line, the Docs sync job's PyYAML install and both self-tests, and the `CLAUDE.md` rule | r5 | Done | This PR. Both self-tests were run in r5 and pass (41 and 11 cases). Mutation-tested here, not assumed from a290: removing the release-shape call from `main()`, removing the config write in `prepare_release.py`, and reversing the version-goes-up comparison each make a self-test fail, and restoring the files makes them pass again |
| 2 | `release.yaml`: only a PR or push that actually moves the version may publish an image (a290-ha-addon/ADR 0006 row 8) | r5 | Done | r5 #133. `scripts/release_decision.py` is copied verbatim from a290 (`cmp`), with its self-test (15 cases) in the `justfile` recipe and the Docs sync job. r5's `release.yaml` has no `validated` job, so only the row-8 parts were ported, and every other line of the file is unchanged. Seven single-point mutants of the script each fail its self-test, including the old rule `publish = is_new`. The workflow's real decision step, extracted and run against a throwaway git remote: an ordinary change, a version moved back to a released one, a tag absent with the version unchanged, and a PR into a non-default branch each give `publish=false`; a release gives `publish=true`; an unreachable remote exits 1. Live check below |
| 3 | First batched release: the renault-api 0.5.14 and pyjwt 2.15.1 bumps already sit under `## Unreleased` with no version change; I cut them as one release when I ask | r5 | Open | Waits for my request, and for the end of this porting work |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context (2026-10-03)

r5's `release.yaml` builds and pushes the image on the PR that moves the version, and a required check blocks the merge until the image is pullable; on push to `main` it republishes and tags `v<version>`. That sequence is not the fault. The fault was a rule that made every merged change move the version, as in a290.

r5 tracks two files the Supervisor reads from `main` besides the changelog and docs: `renault_5/config.yaml` and `renault_5/apparmor.txt`. It tracks no `build.*` file and no other `config.*` file (checked with `git ls-files`, 2026-10-03). The `CLAUDE.md` paragraph on what reaches users on merge applies to both.

`CLAUDE.md` used to say `main.py` carries a `VERSION` constant to keep in sync with `config.yaml`. It does not: `main.py` reads `R5_VERSION`, which the Dockerfile sets from `BUILD_VERSION`. The new rule drops that sentence.

## Consequences

The same as a290-ha-addon/ADR 0006: between releases `main`'s README and DOCS can describe behaviour that has not shipped; a security or dependency fix waits under `## Unreleased` until I ask; the guard runs from the PR it judges, so it is a safety net against ordinary mistakes and not a security boundary; and until row 2 lands, a failed post-merge gate on a release leaves a window in which an ordinary PR can overwrite the released image tag. r5 has no release in that state today.

## Verification

What was checked in r5, and what it could have returned instead.

- Both ported scripts are byte-identical to a290-ha-addon's `main` (`cmp`), and their self-tests pass in r5 against this repo's pinned PyYAML 6.0.3.
- The three mutations in row 1 each failed a self-test for the stated reason (a feature PR that moves the version exited 0; the release edit was not written; a real release was refused as "not an increase"), so the self-tests can fail here.
- `just release 99.0.0 --dry-run` on the real tree wrote nothing.
- **Live check of row 2 (2026-10-03).** A throwaway branch carrying the never-tagged version 1.8.99 on both base and head, and a same-repo PR whose head changed only a comment, against that branch, never touching `main`. The Prepare log shows `HEAD_VERSION` 1.8.99 and `BASE_INFO_VERSION` "1.8.99" (quoted, as the helper prints it), the second checkout at the base branch's commit, `is_new=true`, `version_changed=false`, `publish=false`; both image builds ran with `push: false`; the manifest and tag jobs were skipped; the release gate passed with "Nothing to publish". The three GHCR packages (`renault_5`, `amd64-renault_5`, `aarch64-renault_5`) were listed before and after: the same 546, 316 and 316 versions, no 1.8.99 tag, and `v1.8.99` is absent from the repository. The PR and both branches were then deleted. **This proves the wiring, not the rule alone:** the PR's base is not `main`, so the off-default-branch rule makes `publish` false by itself; the test shows the second checkout resolves the base commit, the helper reads `./base/renault_5`, the step runs and reads `version_changed` as false, but not that the version rule alone would have stopped a push. a290-ha-addon's own live test ran before that rule existed.
- `workflow_dispatch` now never publishes: the comparison base is the commit itself, so the version has not moved. This follows from the ported file and was not tested on Actions.

What this does not establish: that a real version move still publishes and tags (the positive path was not run, and waits for a requested release, row 3); the record of the real push run after #133 merges and of #133's own decision (an ordinary change, `publish=false`) is to be added once they exist; and that the first real release goes through `release.yaml` exactly as before (row 3 is where that is confirmed), or that the guard is complete: a290-ha-addon/ADR 0006 states its limits and they apply here unchanged.
