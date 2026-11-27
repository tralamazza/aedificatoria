# 0029. Incrementally update the temporary JS/TS repo index

- Status: accepted
- Date: 2026-11-27

## Context

ADR 0028 cut Vite history to ~121s by incrementally updating per-file metrics,
but dependencies and package edges still rebuilt the JS/TS `RepoIndex` from the
whole tree every commit. History reuses one worktree and knows the changed paths.

## Decision

- Add `RepoIndex.update(changes)`: remove old source files, parse added/modified
  source files, and reload manifests/configs only when a `package.json`,
  `tsconfig.json`, or `jsconfig.json` changes.
- Keep the cached `RepoIndex` across a history chunk via
  `baseline.collect(..., preserve_index=True)` and
  `baseline.update(..., preserve_index=True)`.
- Before each incremental update, apply the same `diff_paths` changes to the
  index. Skip generated directories during source updates.
- Clear the index cache at chunk end and for normal/full collections.
- Keep dependency/edge aggregation over the index; only index construction is
  incremental.

## Alternatives considered

- Recompute dependency/edge aggregates by applying per-file diffs. Deferred:
  package/alias resolution can change globally when manifests/configs change;
  the current full aggregation over an updated index is safer.
- Persist the index across chunks/runs. Rejected: worktree lifetime and memory
  ownership are per chunk.
- Cache per-file import facts only. Rejected: it would still require a full path
  walk and package/config rebuild each commit.

## Consequences

Vite 1k dropped from ~121s to ~94s (530s originally). Changed source files are
parsed once, manifests/configs are rare full reloads, and graph aggregation still
walks the in-memory index. Index memory is per chunk/worker. Full collections
continue to clear the cache.

## Invariants

- A source change removes the old path and parses the new path exactly once.
- Generated directories are not added during incremental updates.
- Any manifest/config change reloads those dimensions from the tree.
- The index is preserved only within a history chunk.
- Normal `baseline.collect`/full checks clear the index cache.
- Full and incremental metrics/edge sets match.

## Rollback

Remove `RepoIndex.update`, `preserve_index`, and history index wiring; rebuild a
fresh index every collect.
