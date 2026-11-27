# 0017. Detect merge-only conflict resolution lines

- Status: accepted
- Date: 2026-11-27

## Context

A merge commit's first-parent net diff includes conflict-resolution edits, but
attributes them to the merge. `--all` analyzes the branch commits against their
own parents, so it does not isolate edits introduced only while resolving the
merge. A conflict resolution can add architecture changes that exist on neither
parent. The history report had no signal for this.

## Decision

Add `git.merge_edited_files`: run `git show --cc --format=` for a multi-parent
commit and collect files containing lines prefixed entirely with `+` or `-`.
Those prefixes mean the line was added or removed relative to **every** parent.
Emit a report-only `warn` finding:

`merge-only lines in N file(s) (potential conflict resolution): ...`

The finding is added to both first-parent and `--all` history points, weighted 4
in the impact score. It is not a gate.

This intentionally does not flag:
- same-file auto-merges that only combine lines from the parents;
- resolutions that choose one parent exactly (the result is not merge-only);
- merges with no combined-diff lines.

## Alternatives considered

- Intersect files changed against each parent. Rejected: flags ordinary
  non-overlapping same-file auto-merges as conflict resolutions.
- Use the first-parent net diff alone. Rejected: conflates branch changes with
  conflict-resolution changes.
- Re-run the merge to reconstruct conflicts. Rejected: non-hermetic and may
  produce a different result than the recorded merge.
- Parse combined-diff hunk bodies for context. Rejected for now: line-level
  all-plus/all-minus prefixes are deterministic and cheaper.

## Consequences

Novel conflict-resolution/manual merge lines now surface in history. Pure
reorders and conflict resolutions that exactly select one parent are not flagged;
binary conflicts may not produce combined-diff lines. The signal is heuristic,
so the message says “potential conflict resolution.” First-parent and `--all`
both include it; the merge commit remains a net diff.

## Invariants

- Only multi-parent commits are inspected.
- Only lines changed relative to every parent are counted.
- Same-file auto-merges that combine parent lines do not produce the finding.
- The finding is report-only and never runs in `aed check`.
- Squash/single-parent atomic warnings are unchanged.

## Rollback

Remove `merge_edited_files`, `_merge_edit_finding`, its two call sites, the impact
weight, and the README paragraph.
