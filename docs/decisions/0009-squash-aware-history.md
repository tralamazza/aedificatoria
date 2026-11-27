# 0009. Squash-aware history and pre-merge auditing

- Status: accepted
- Date: 2026-11-27

## Context

`aed history` compared first-parent commits. A squash merge collapses an entire
PR into one single-parent commit, so the intermediate commits never appear on
main and cannot be reconstructed from Git. A large squash can therefore look
like one atomic regression, or hide simple attribution. This is a property of
the merge strategy, not something a history walker can undo.

## Decision

Make history squash-aware and move the authoritative audit to the PR branch:

- Record churn (files and changed lines) for every commit, comparing against its
  first parent.
- Mark single-parent commits whose subject ends in `(#N)` as `squash` and keep
  the PR number.
- Mark commits with multiple parents as `merge`.
- Warn when a single-parent commit exceeds `max_changed_files` or
  `max_changed_lines`, stating that it is a large atomic change and its internal
  commits are unavailable.
- Show kind, churn, and the squash marker in the timeline and the ranked
  regressions.

Do not call GitHub or any network service to recover squashed commits. The tool
stays offline and Git-only. Instead, document the correct workflow: run
`aed history --since <base>` and `aed check --base <base>` on the PR branch before
squashing, or use a merge/rebase merge when the internal history matters. The
existing push/pull-request CI already audits the PR range before merge.

## Alternatives considered

- Recover PR commits from GitHub's API. Rejected: network, credentials, and
  repository-host coupling in a hermetic guard.
- Analyze deleted branch refs. Rejected: squashes usually delete the branch, and
  local refs are not portable.
- Flag every single-parent commit. Rejected: too noisy; only oversized ones are
  actionable.
- Require merge commits. Rejected: teams own their merge strategy; the guard
  should surface the tradeoff, not dictate hosting policy.

## Consequences

Large squashes are visible and explicitly carry the "internal commits
unavailable" warning. Attribution remains coarse for squashed changes; the
pre-merge audit is the authoritative record. Small squashes are not flagged
unless they also produce metric regressions. The detection of `(#N)` is a
heuristic; custom squash messages still get the size warning because any
oversized single-parent commit is treated as atomic.

## Invariants

- History remains read-only and exits 0.
- Churn is computed against the first parent.
- Merge commits are never reported as squashes.
- No network or GitHub API calls.
- The push/PR guard remains the blocking gate; history is diagnostic.

## Rollback

Remove churn, kind, and atomic-warning fields from `HistoryPoint`, the squash
heuristic, and this ADR.
