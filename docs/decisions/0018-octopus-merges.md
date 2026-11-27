# 0018. Audit octopus merges with the generic N-parent path

- Status: accepted
- Date: 2026-11-27

## Context

Octopus merges have three or more parents. The history code classifies any
multi-parent commit as `merge`, but the combined-diff parser had only been
exercised on two-parent merges. Git's combined diff prefixes have one column per
parent, so a three-parent merge uses three prefix characters and a four-parent
merge uses four. The parser must not assume two.

Git's octopus strategy also refuses content conflicts, so ordinary octopus merges
are usually clean. Manually constructed octopus commits can still contain
merge-only conflict-resolution lines and must be inspectable.

## Decision

Keep the generic N-parent path rather than special-casing octopus:

- `kind` is `merge` for every commit with more than one parent;
- `diff_stats` remains the net diff against the first parent;
- `--all` walks every reachable parent branch;
- large merge warnings use the same file/line thresholds;
- `merge_edited_files` reads the parent count from `commit_parents` and uses that
  as the combined-diff prefix width. Lines prefixed entirely with `+` or `-` are
  merge-only edits.

Add regression tests for a clean four-parent merge (classification, `--all`
coverage, no false merge-only finding) and a synthetic four-parent merge with a
merge-only file (prefix-width parsing).

## Alternatives considered

- Special-case only two-parent merges. Rejected: octopus merge-only edits would
  be invisible.
- Treat all parents as a flattened diff. Rejected: loses first-parent mainline
  semantics and cannot distinguish conflict resolution.
- Rerun the octopus merge. Rejected: non-hermetic; octopus refuses conflicts.

## Consequences

Octopus merges share the same first-parent/`--all` behavior as two-parent merges.
The merge commit remains a net diff against its first parent. Conflict-resolution
detection works for any parent count, subject to the existing caveats
(reorders, exact parent selection, some binaries). No gate changes.

## Invariants

- Any commit with more than one parent is `kind=merge`.
- Combined-diff prefix width equals the parent count.
- Clean octopus merges do not produce merge-only findings.
- `--all` includes all parent branches.
- The merge commit remains a first-parent net diff.

## Rollback

Remove the octopus regression tests and this documentation; the production code
already uses the generic N-parent path.
