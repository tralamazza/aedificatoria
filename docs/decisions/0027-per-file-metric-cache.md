# 0027. Per-file metric fact cache

- Status: accepted
- Date: 2026-11-27

- Amended by: 0028 (incremental history updates)

## Context

After worktree reuse and parse-tree caching, Vite 1k history dropped to 213s,
but profiling still showed per-file test/complexity/API traversal as the next
cost. History processes many commits that share almost all file contents. The
same file was re-analysed from its cached syntax tree on every commit.

## Decision

Cache raw per-file facts in `baseline._file_facts`:

- Key: `(adapter name, file extension, exact source text)`.
- Value: `TestInfo`, complexity map, and public API map.
- Process-local LRU, 4,096 entries.
- Cache only analysis independent of `Config`; `is_test`, `is_api`, and
  `is_metric_excluded` remain outside the cache.
- Python `__init__.py` public surfaces are still resolved separately because
  they depend on the repository tree.
- Also cache `RepoIndex` for immutable temporary trees (`tree-aed-*`) so the
  dependency and package-graph passes share one scan. Clear that cache before
  and after every `baseline.collect`; a reused history worktree can change
  contents at the same path, so root-only keys are otherwise stale.
- No persistent/disk cache; no cross-run invalidation contract yet.

## Alternatives considered

- Cache full metric dictionaries. Deferred: config and package classification
  must remain commit-specific, and the raw file facts are the safe unit.
- Disk cache keyed by Git blob. Rejected for now: cross-run storage, eviction,
  and schema/version invalidation are a separate design.
- Key by file path and mtime. Rejected: temp trees have different mtimes and
  paths; exact source text is deterministic.
- Cache only parsed trees. Rejected: traversal costs remain; the parse cache
  already covers parsing.

## Consequences

Warm `baseline.collect` on Vite dropped from ~1.3s to ~0.63s with the file facts,
and dependency/package scans share one `RepoIndex` per collect. The fact cache is
per worker and per process; different workers and runs repeat the first
analysis. Memory is bounded by the LRUs. Output is unchanged. Config changes
cannot accidentally reuse a classification result because filters are applied
after lookup. The temp-index cache is explicitly cleared around each collect, so
worktree reuse cannot serve a previous commit's index.

## Invariants

- Cache key includes adapter, extension, and exact text.
- Changing any file byte produces a different key.
- Cached values are read-only analysis facts; callers copy before storing.
- Config filters are evaluated outside the cache.
- Python package export resolution is not served from this cache.
- Temp-tree `RepoIndex` caches are cleared before and after every collect.
- Real working-tree roots are never served from the temp index cache.
- No filesystem persistence.

## Rollback

Remove `_file_facts`, its imports, and its call in `_collect_file`.
