# Real-world audit

Run: 2026-11-27. Local clones, first-parent history, no repository writes.
Findings are history metric regressions, not `aed check` verdicts.

## 1,000-commit windows

| Project | Language | Window | Time | Findings | Commits with findings |
|---|---|---|---:|---:|---:|
| `pallets/click` | Python | 2014-08-15..2026-11-16 | 85s | 2,860 | 331 |
| `BurntSushi/ripgrep` | Rust | 2020-02-20..2026-11-24 | 103s | 862 | 193 |
| `vitejs/vite` | TypeScript | 2026-02-26..2026-11-27 | 94s (was 530s) | 1,513 | 393 |
| `expressjs/express` | JavaScript | 2012-07-06..2026-11-03 | 110s | 729 | 214 |

Parallelism was `--jobs 12`, except Express used `--jobs 4` while Vite was
running. After ADR 0026 worktree/parse reuse, ADR 0027 per-file facts, and
ADR 0028 incremental updates and ADR 0029 incremental RepoIndex, Vite
dropped from 530s to 94s (100 commits: 57s to ~14s). Regression sets and metrics
match the full analysis; findings are now deterministically sorted.

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

## P2 hardened rerun

Rerun on the P2 changes (ADR 0037/0038) with the same local clones, `--jobs 4`,
first-parent history:

| Project | Window | Time | Findings | Commits with findings |
|---|---|---:|---:|---:|
| `pallets/click` | 1,000 commits | 88s | 2,858 | 331 |
| `BurntSushi/ripgrep` | 1,000 commits | 184s | 861 | 193 |
| `vitejs/vite` | 1,000 commits | 144s | 1,482 | 378 |
| `expressjs/express` | 1,000 commits | 47s | 728 | 213 |
| `dtolnay/anyhow` | 100 commits | 9s | 14 | 9 |
| `sindresorhus/p-queue` | 100 commits | 6s | 249 | 44 |

Changes versus the previous audit are explainable by the P2 fixes: Click loses
the docs-only dependency/edge, anyhow loses Rust parameter-name private API
noise, p-queue/Express lose JS declaration comment changes, and Vite loses
private/excluded surface noise. No runs crashed. Vite and ripgrep are slower
than the prior optimized pass because source discovery now consults Git and
applies symlink/size/dependency-scope checks; Vite remains under 2.5 minutes for
1,000 commits. `--jobs 1` and `--jobs 4` produce identical p-queue payloads.

## Remaining observations

- Merge-heavy first-parent windows are noisy; `--all` or PR/MR history is better
  for internals.
- Intentional deprecations and typing migrations appear as API regressions under
  zero growth caps. That is diagnostic, not automatically a defect.
- Test rewrites in large migration commits look like test tampering. The guard
  cannot distinguish a deliberate rewrite from a weakened suite without review.
- Vite remains per-file I/O and project-graph aggregation bound; changed files are
  recollected, but dependency/edge aggregation still walks the source inventory.
- History uses the current configuration for every commit; historical policy
  changes are not re-evaluated.
