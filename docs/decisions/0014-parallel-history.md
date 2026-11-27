# 0014. Parallel local history metric collection

- Status: accepted
- Date: 2026-11-27
- Amended by: 0024 (unique worktree basenames); 0025 (serialize worktree admin)

## Context

History reconstruction is embarrassingly parallel at the commit level: each
commit is materialized in its own worktree and reduced to metrics independently.
The first implementation ran commits sequentially. A 100-commit audit of
`pallets/click` took about 27 seconds, which makes longer investigations
unpleasant. The sequential comparison and reporting must stay deterministic.

## Decision

Add `--jobs N` (`-j`) to `aed history` for local history:

- Compute commit metadata (parents, churn) sequentially in commit order.
- Collect per-commit metrics in a `ProcessPoolExecutor` with `N` workers; each
  task gets the repository path, commit SHA, and the frozen `Config`.
- Each worker creates and removes its own temporary worktree via
  `base_worktree`; paths are unique.
- `pool.map` preserves task order; comparison and `HistoryPoint` construction
  happen in the parent in commit order.
- Default is `1`; `N` is clamped to the number of commits. Forge API modes do
  not use this path.
- Workers return plain metric dictionaries, so results are picklable and
  independent of process state.

## Alternatives considered

- Threads. Rejected: Python AST work holds the GIL; tree-sitter parsing alone
  would leave a large serial fraction.
- Async I/O. Rejected: the work is CPU-bound.
- Read blobs/trees directly instead of worktrees. Rejected for now: larger
  change to all adapters; worktree creation is not the dominant cost.
- Persistent cache across runs. Rejected: invalidation and storage complexity;
  `jobs` gives most of the benefit without state.
- Parallel forge API downloads. Rejected: rate limits, auth, and ordered
  tarball extraction make it a separate design.

## Consequences

Measured on `pallets/click`: 20 commits 5.7s → 1.15s at 8 workers; 100 commits
27.1s → 4.36s at 8 workers (CLI end-to-end about 6s). JSON output was
byte-identical to the sequential run and the metric digest matched. Costs:
process startup/import, one parser tree per worker, and concurrent Git worktree
operations. `jobs=1` remains the default and has no multiprocessing overhead.
Worker exceptions propagate to the parent.

## Invariants

- `jobs=1` and `jobs=N` produce identical `HistoryPoint` content.
- Commit order is preserved; comparison is sequential in the parent.
- Each worker uses a unique temporary worktree and removes it.
- No parallel network access; forge modes ignore `jobs`.
- The frozen `Config` and metric dictionaries stay picklable.

## Rollback

Remove the `jobs` parameter and CLI flag, and restore the sequential metric
collection loop.
