# 0019. Rebase merges are linear ordinary commits

- Status: accepted
- Date: 2026-11-27

## Context

Rebase-and-merge rewrites the pull request's commits onto the base branch and
fast-forwards: there is no merge commit. Unlike a squash, every internal commit
is preserved (with new SHAs). Unlike a merge, there is no second parent and no
combined diff.

Git does not record that a linear commit series came from a rebase. Conflict
resolutions during a rebase are baked into the rewritten commits and look like
ordinary commit changes. GitHub's `(#N)` squash marker is also present on some
single-parent commits, so a rebased commit whose subject ends in a PR number can
be mistaken for a squash.

## Decision

Treat rebase merges as ordinary one-parent history:

- default first-parent history already examines every commit;
- `--all` adds no special coverage for a clean linear rebase;
- no `large merge` or `merge-only` findings apply;
- rebase conflict resolutions are analyzed as normal commit changes, not as
  separately attributable merge edits.

Keep the squash marker as a documented heuristic; do not invent a rebase
detector from committer dates, author dates, or PR suffixes, which are not
reliable. Use PR/MR history when the pre-rebase sequence or original SHAs matter.

## Alternatives considered

- Detect rebase from committer/author date skew. Rejected: both rebase and squash
  rewrite committer data, and normal workflows can also differ.
- Treat every `(#N)` single-parent commit as a rebase instead of a squash.
  Rejected: would misclassify real squashes.
- Add a merge commit for rebases. Rejected: changes repository history and is not
  the guard's job.

## Consequences

A clean rebase is the best preserved history for first-parent analysis. Rebase
conflict resolutions are visible in metrics but not labeled as conflict
resolutions. A rebased commit ending in `(#N)` may still get the squash-style
large-commit warning; the warning and README call the marker a heuristic.

## Invariants

- Rebase commits are `kind=commit` unless their subject matches the squash marker.
- A clean rebase is fully visible in first-parent history.
- No merge-only signal is emitted for single-parent commits.
- Original SHA recovery remains a PR/MR-ref concern, not a local-history feature.

## Rollback

Remove the rebase regression test and this documentation; no production code
change is required.
