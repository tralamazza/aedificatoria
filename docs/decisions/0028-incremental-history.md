# 0028. Incremental history baseline updates

- Status: accepted
- Date: 2026-11-27

- Amended by: 0029 (incremental RepoIndex)

## Context

After per-file caches, Vite history still walked and read every source file for
every commit. History chunks process consecutive commits in one reused worktree,
so most paths are unchanged between adjacent commits. The full metric snapshot
from the previous commit is already available in the worker.

## Decision

- Keep one full `baseline.collect` per chunk (first commit).
- For subsequent commits, compute `git diff --name-status --find-renames`
  between adjacent commits.
- Start from the previous full snapshot; remove entries for changed/deleted/old
  paths, collect only added/modified paths, then recompute project dependencies
  and package edges.
- Skip generated directories (`node_modules`, caches, build outputs) and config
  excludes during recollection, matching the full file walk.
- Use the same path for full and incremental snapshots. Sort history findings by
  severity/path/line/rule/message so map insertion order cannot change JSON.
- A regression test compares `baseline.update` to a fresh full `collect` across
  modified, added, deleted, and test-file changes.

## Alternatives considered

- Cache every file by Git blob hash. Deferred: a larger storage/invalidation
  design; the previous full snapshot already provides the needed state.
- Incrementally rebuild dependency/package graphs too. Deferred: graph edge
  changes need more careful invalidation; current full recomputation stays
  correct.
- Cache complete commit snapshots by SHA. Rejected: memory grows with history.
- Keep reading unchanged files to use the fact cache. Rejected: the fact cache
  avoids analysis but not I/O and source-file walking.

## Consequences

Most commits re-read only changed source files. Project-level dependency/package
scans remain full per commit and are the next optimization target. Full and
incremental metric snapshots compare equal; history output is deterministic after
sorting. A chunk's first commit still pays a full scan.

## Invariants

- Adjacent commits use the diff from the same pair of SHAs.
- Changed paths are removed from every per-file section before recollecting.
- Generated dirs and config excludes are skipped during recollection.
- Deleted paths leave no metrics behind.
- Dependencies/edges are recomputed from the new tree.
- Full and incremental snapshots are equal for the same tree/config.
- History findings are sorted independently of insertion order.
- Worktree reuse and cache clearing remain in effect.

## Rollback

Use `baseline.collect` for every commit again and remove `diff_paths`/`update`
and finding sorting.
