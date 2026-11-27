# 0008. History audit for existing repositories

- Status: accepted
- Date: 2026-11-27
- Amended by: 0015 (--all, impact ranking, API scope)

> Later: first-parent remains the default; `--all` adds side-branch commits per
> ADR 0015. The first-parent consequence below describes the default only.

## Context

The persistent baseline stops future drift, but an existing repository already
carries unknown debt. New adopters need to know which past commits introduced
complexity, public API, dependency, coupling, and test regressions, without
rewriting history or running the full guard on every commit.

## Decision

Add `aed history`, backed by `history.py`:

- Walk the last `--limit` first-parent commits (default 20), or commits after
  `--since`, oldest first.
- Reconstruct each commit in a temporary detached worktree and compute the same
  metrics as the persistent baseline.
- Compare consecutive commits with all growth caps forced to zero, and report
  per-function complexity growth, total complexity growth, public API removals
  and growth, new dependencies, new package edges, removed tests, and assertion
  drops.
- Detect package cycles that are new relative to the previous commit.
- Print a timeline plus a ranked "where it went bad" section; `--json` emits the
  same data.

This is a report, not a gate: it exits 0 and never writes the repository. It uses
the current configuration for all commits, so results are comparable across the
window. It does not re-evaluate historical ADRs, one-way doors, or config
weakenings; the history command answers metric questions, not policy questions.

## Alternatives considered

- Run the full guard for every commit. Rejected: it needs a base and head
  worktree per commit, historical configs make verdicts ambiguous, and hooks,
  baselines, and ADRs did not exist for most commits.
- `git bisect` on one metric. Rejected: only one metric at a time and no
  timeline.
- External repository analytics. Rejected: extra service and no access to the
  guard's metric definitions.

## Consequences

History audits cost roughly `limit × repository size`; twenty commits on a small
repository take under a second, larger repositories proportionally longer.
First-parent (the default) means merge commits and side branches are skipped;
`--all` (ADR 0015) adds side branches. The report uses current config and zero
caps, so it intentionally surfaces growth the baseline has already accepted.
Renames appear as removals plus additions.

## Invariants

- History evaluation is read-only and always exits 0.
- Commits are reported oldest first; ranking is stable.
- Temporary worktrees are removed.
- The command needs no baseline file.
- Metrics are the same definitions as the baseline.

## Rollback

Delete `history.py`, the `history` subcommand, its tests, and this ADR.
