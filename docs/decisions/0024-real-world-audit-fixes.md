# 0024. Fixes from real-world history audits

- Status: accepted
- Date: 2026-11-27

- Amended by: 0025 (serialize worktree administration)

## Context

A first real-world pass over Click (Python), anyhow (Rust), p-queue
(TypeScript), and Express (JavaScript) exposed three issues:

1. Express failed at `--jobs 8` because every temporary worktree used the
   basename `tree`; concurrent Git worktrees collided under
   `.git/worktrees/tree`.
2. Click reported “complexity increased 28 -> 28” for functions already above
   `max_complexity` but unchanged in the commit. Existing debt was blocking
   unrelated edits.
3. anyhow reported `Ok(t: T)` -> `Ok(value: T)` as a public API break. Rust
   parameter names are not part of the callable API.

## Decision

- Give every temporary worktree a unique basename derived from its temp root.
  Concurrent history workers must never share a Git worktree ID.
- For an existing function, emit a `complexity` finding only when growth exceeds
  `complexity_delta`. The absolute `max_complexity` continues to apply to new
  functions. Existing debt is handled by the baseline ratchet.
- Normalize Rust public signatures using parameter types, receivers, return
  types, and type parameters; omit parameter names.

## Alternatives considered

- Serialize worktree creation. Rejected: removes the parallel benefit.
- Keep flagging every existing function over the absolute max. Rejected:
  unrelated edits to old functions become blocked; the baseline already ratchets
  existing debt.
- Strip parameter names textually. Rejected: type syntax can contain identifiers;
  AST extraction is safer.
- Treat Rust parameter names as API. Rejected: Rust callers do not use parameter
  names.

## Consequences

Parallel history is reliable on merge-heavy shallow clones. Existing
over-complex functions can still be flagged when they grow by more than the
delta, while new functions are checked against the absolute limit. Rust API
comparison ignores renames but still catches type/return/receiver changes.
Baseline metrics change for Rust signatures and must be refreshed.

## Invariants

- Concurrent worktrees have unique filesystem basenames and Git IDs.
- New functions are checked against `max_complexity`.
- Existing functions require growth greater than `complexity_delta`.
- Rust parameter names do not affect API surfaces.
- Rust receiver/type/return/type-parameter changes still affect API surfaces.
- Worktrees are removed after success and failure.

## Rollback

Restore shared worktree naming, the absolute-max check for existing functions,
and raw Rust parameter text; revert the audit report and baseline.
