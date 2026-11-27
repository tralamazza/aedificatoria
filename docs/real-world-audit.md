# Real-world audit

First pass: 2026-11-27. Local clones, first-parent history, `--jobs 8`, no
repository writes.

| Project | Language | Window | Time | Findings | Main signal |
|---|---|---|---:|---:|---|
| `pallets/click` | Python | 100 commits, 2026-04-12..2026-11-16 | 10s | 361 | API typing changes, merge/test topology, complexity noise |
| `dtolnay/anyhow` | Rust | 100 commits, 2024-09-06..2026-11-22 | 4s | 19 | low noise; false API signature from parameter rename |
| `sindresorhus/p-queue` | TypeScript | 100 commits, 2023-08-30..2026-10-13 | 4s | 258 | large Node support migrations, test churn, API renames |
| `expressjs/express` | JavaScript | 100 commits, 2025-10-16..2026-11-03 | 8s | 26 | squash/test churn, security revert |

Express initially failed at `--jobs 8` with
`failed to read .git/worktrees/tree3/commondir`; `--jobs 1` worked. Root cause:
every temporary worktree used the basename `tree`, so concurrent Git worktrees
collided under `.git/worktrees/tree`. Unique basenames fix it; the audit then
completed at 8 jobs.

## Targeted checks

| Commit | Result after fixes |
|---|---|
| Click `8a2b489` | 3 real `public-api` errors from `ParamType` typing changes; prior 9 equal-score complexity errors removed |
| anyhow `b9009ab` | `allow`; the prior `Ok(t)` / `Ok(value)` Rust parameter-name “break” is gone |
| p-queue `b2600d5` | 2 expected errors: Node 20 migration exceeds the diff budget and rewrites tests |
| Express `697547c` | 2 expected errors: a security revert removes 9 tests/assertions |

## Fixed from this pass

1. Parallel history worktree collision (Express).
2. Unchanged functions already above `max_complexity` were reported as
   “complexity increased N -> N” (Click).
3. Rust parameter names were treated as public API; only types, receivers,
   return types, and type parameters are API (anyhow).

ADR: [`docs/decisions/0024-real-world-audit-fixes.md`](decisions/0024-real-world-audit-fixes.md).

## Remaining observations

- Merge-heavy first-parent windows are noisy; `--all` or PR/MR history is better
  for internals.
- Intentional deprecations and typing migrations appear as API regressions under
  zero growth caps. That is diagnostic, not automatically a defect.
- Test rewrites in large migration commits look like test tampering. The guard
  cannot distinguish a deliberate rewrite from a weakened suite without review.
- p-queue’s Node support migration is a 3,441-line commit; change-amplification
  correctly flags it.
- Express’s security revert intentionally removes tests; test-tampering
  correctly blocks until reviewed.
