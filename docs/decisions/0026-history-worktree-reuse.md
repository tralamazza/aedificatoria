# 0026. Reuse worktrees and parse caches for history

- Status: accepted
- Date: 2026-11-27

- Amended by: 0027 (per-file metric fact cache)

## Context

The 1,000-commit Vite audit took 530s. Profiling showed repeated full-tree
checkout, duplicated TS parsing, and three separate `RepoIndex` scans per commit:

- worktree add/remove: ~0.46s per commit;
- index checkout: ~0.27s per commit;
- reused worktree checkout: ~0.06s per commit;
- Vite metric collection: ~1.8s per commit, including four parses of the same
  1,700 files.

## Decision

- Split ordered history commits into contiguous chunks, one per worker.
- Each worker creates one reusable worktree, checks out its first commit, then
  checks out subsequent commits in place with `git checkout --detach --force`.
  Worktree add/remove remains serialized per repository (ADR 0025).
- Flatten chunk results in chunk order so `jobs=1` and `jobs=N` produce identical
  output.
- Cache JS/TS parsed trees by `(grammar, source text)` in a process-local
  4,096-entry LRU. Trees are read-only analysis artifacts; unchanged files are
  reused within a worker.
- Keep the base-tree index checkout for `aed check`; history uses reusable
  worktrees because it needs to advance through many commits.

## Alternatives considered

- Fresh worktree per commit. Rejected: 530s for Vite.
- Fresh index tree per commit. Rejected: 44s/100, still materialization-bound.
- Reuse one worktree for the entire run. Rejected: loses parallel metric
  collection.
- Persistent cross-run cache. Deferred: invalidation and storage contract are
  separate work.
- Cache complete metric dictionaries. Deferred until parse/checkout costs are
  lower; the tree cache is simpler and safe.

## Consequences

Vite materialization drops from four full parses and a full checkout per commit
to one initial checkout per worker, incremental checkouts, and cached trees.
Analysis still traverses every file for metrics, which becomes the next target.
The parse cache holds tree-sitter trees per worker; the LRU bounds memory.

## Invariants

- Chunks are contiguous and ordered; flattened output matches sequential.
- Every chunk worktree is removed on success and failure.
- Worktree add/remove is still serialized per repository.
- Cached trees are keyed by grammar and exact source text.
- `aed check` continues to use the base index tree, not a worktree.
- No network or repository writes.

## Rollback

Restore per-commit metric tasks, remove chunking and `_worktree_tree`, and drop
the `jsts` parse cache.
