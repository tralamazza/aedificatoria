# 0038. Bounded glob performance for history audits

- Status: accepted
- Date: 2026-11-27

## Context

The ADR 0036 bounded glob matcher replaced regex matching with dynamic
programming everywhere. Real-world Vite history then spent over five minutes on
a 100-commit window, versus roughly 14 seconds before the hardening. Repeated
source enumeration across adapters also walked complete temporary trees for
every metric pass.

## Decision

- Keep regex matching for ordinary patterns, but only below a small wildcard and
  length budget; use the bounded DP matcher for complex patterns.
- Cache path/pattern match results and compiled ordinary patterns.
- Cache file lists only for temporary trees created by aed; prime after materializing
  a base/checkout tree and invalidate after each history checkout. Ordinary
  working trees are never served from that cache.
- Keep Git-aware enumeration for real working trees, but walk temporary trees
  that contain only tracked files.

## Consequences

Normal real-world history stays fast while pathological patterns remain on the
bounded path. Vite's 100-commit window returned to ~18s and the 1,000-commit
window to ~144s. The cache is scoped to aed-created temporary trees, avoiding
stale results for arbitrary directories named like temp trees.

## Alternatives considered

- Revert to unbounded regex matching. Rejected: reintroduces config-controlled
  ReDoS.
- Use DP for every pattern. Rejected by the measured Vite regression.
- Cache working-tree file lists globally. Rejected: silent stale results after
  normal edits.

## Rollback

Restore DP matching for all patterns and remove temporary-tree file caches,
accepting slower history audits and repeated walks.
