# Real-world audit

Run: 2026-11-27. Local clones, first-parent history, no repository writes.
Findings are history metric regressions, not `aed check` verdicts.

## 1,000-commit windows

| Project | Language | Window | Time | Findings | Commits with findings |
|---|---|---|---:|---:|---:|
| `pallets/click` | Python | 2014-08-15..2026-11-16 | 85s | 2,860 | 331 |
| `BurntSushi/ripgrep` | Rust | 2020-02-20..2026-11-24 | 103s | 862 | 193 |
| `vitejs/vite` | TypeScript | 2026-02-26..2026-11-27 | 158s (was 530s) | 1,513 | 393 |
| `expressjs/express` | JavaScript | 2012-07-06..2026-11-03 | 110s | 729 | 214 |

Parallelism was `--jobs 12`, except Express used `--jobs 4` while Vite was
running. After ADR 0026 worktree/parse reuse and ADR 0027 per-file facts,
Vite dropped from 530s to 158s (100 commits: 57s to 18s). The optimized runs
produced byte-identical JSON.

## Smaller controls

| Project | Language | Window | Time | Findings |
|---|---|---|---:|---:|
| `dtolnay/anyhow` | Rust | 100 commits, 2024-09-06..2026-11-22 | 4s | 19 |
| `sindresorhus/p-queue` | TypeScript | 100 commits, 2023-08-30..2026-10-13 | 4s | 258 |

## Signals

- Click: 2,018 API removals/signatures, 335 function-complexity and 250 total
  complexity findings; large historical refactors and merge nets dominate.
  Top: a 6,977-line merge with 541 regressions.
- ripgrep: 439 API removals/signatures and 16 large atomic warnings. Top: the
  18,865-line clap→lexopt replacement.
- Vite: 942 of 1,000 commits are squashes; 59 large atomic warnings, 408 test
  inventory changes, 399 API changes, 260 function-complexity changes.
- Express: 434 test inventory changes, 46 merge-only findings, 15 large merges.
  Test rewrites and reverts dominate.

## Fixed from this pass

1. Worktree collision. Unique basenames fixed per-path collisions; 1,000 Click
   commits at 12 workers still failed on shared `.git/worktrees` metadata.
   Worktree add/remove is now serialized by a per-repository `flock`, while
   metric collection remains parallel.
2. Unchanged functions above `max_complexity` were reported as “complexity
   increased N -> N.” Existing functions now require growth over
   `complexity_delta`; new functions still use the absolute max.
3. Rust parameter names were treated as public API. Signatures now compare
   parameter types, receivers, returns, and type parameters.

ADRs: [`0024`](decisions/0024-real-world-audit-fixes.md),
[`0025`](decisions/0025-serialize-worktree-admin.md).

## Targeted full checks after fixes

| Commit | Result |
|---|---|
| Click `8a2b489` | 3 real `public-api` errors from `ParamType` typing changes; previous equal-score complexity errors removed |
| anyhow `b9009ab` | `allow`; prior `Ok(t)` / `Ok(value)` parameter-name break gone |
| p-queue `b2600d5` | 2 expected errors: Node 20 migration exceeds budget and rewrites tests |
| Express `697547c` | 2 expected errors: security revert removes 9 tests/assertions |

## Remaining observations

- Merge-heavy first-parent windows are noisy; `--all` or PR/MR history is better
  for internals.
- Intentional deprecations and typing migrations appear as API regressions under
  zero growth caps. That is diagnostic, not automatically a defect.
- Test rewrites in large migration commits look like test tampering. The guard
  cannot distinguish a deliberate rewrite from a weakened suite without review.
- Vite-scale repositories remain metric-traversal-bound after worktree reuse;
  further speedup requires caching per-file metrics, not more checkout tuning.
- History uses the current configuration for every commit; historical policy
  changes are not re-evaluated.
