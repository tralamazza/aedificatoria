# 0016. Flag large merge net diffs as recoverable

- Status: accepted
- Date: 2026-11-27

## Context

Squash commits are atomic: their internals are gone from the repository. Merge
commits are different. A merge net diff can be arbitrarily large, but its branch
commits remain reachable through the second parent. The history atomic check
currently ignores every multi-parent commit, so a large merge silently dominates
first-parent churn without warning the user that the internals are available.

## Decision

When a multi-parent commit exceeds `max_changed_files` or `max_changed_lines`,
emit a warning: `large merge: N files, L changed lines; branch commits remain in
history (use --all)`. Do not call it atomic, and do not claim the internals are
unavailable. Single-parent commits keep the existing large-atomic/squash warning.

The warning is report-only and participates in impact ranking as an ordinary
finding. `--all` still compares each side-branch commit to its first parent; the
merge commit itself remains a net diff.

## Alternatives considered

- Ignore merges. Rejected: large merges dominate first-parent findings and hide
  whether the size is intrinsic or just branch topology.
- Treat merges as atomic. Rejected: false; their internals are intact.
- Skip merge net diffs entirely. Rejected: the merge still changes the branch,
  and users need its net size to decide whether to audit the side branch.

## Consequences

First-parent history now surfaces large merges as candidates for `--all` or PR
auditing. No gate changes. A repo with many large merges may show more warnings;
the warning is informational, not a failure.

## Invariants

- Merge warnings never say internal commits are unavailable.
- Squash/single-commit warnings are unchanged.
- `--all` includes side-branch commits and therefore the merge's internals.
- Warning thresholds reuse `max_changed_files`/`max_changed_lines`.

## Rollback

Remove the multi-parent branch from `_atomic_finding` and the README paragraph.
