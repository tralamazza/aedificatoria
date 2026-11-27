# 0015. History signal: all commits, real API scope, signatures, impact

- Status: accepted
- Date: 2026-11-27

## Context

A full 1,392-commit audit of Click exposed four signal problems:

- first-parent history analyzed only 1,392 of 3,416 commits; 65% of findings were
  merge nets, and side-branch changes were invisible;
- 908 of 2,097 findings were API removals, but 268 were in private `_` modules;
- public API stored only symbol names, so same-name signature breaks were
  invisible to the baseline/history;
- examples and `docs/conf.py` counted as production metrics, and raw finding count
  ranked large merges above dense small regressions.

## Decision

1. Add `aed history --all`. It walks all reachable commits with parents
   (topological, oldest first), collects metrics in the same process pool, and
   compares each commit to its first parent. It captures side branches. Merge
   commits are still net diffs against their first parent.
2. Exclude private paths from public API. `Config.is_private` treats any path
   segment starting with `_` (except `__init__.py`) as private; `is_api` returns
   false for those files.
3. Add `metric_excludes` (default `docs/**`, `examples/**`, `benchmarks/**`).
   Baseline collection and the per-diff complexity/public-API checks skip these
   files. It is separate from `exclude`, so ADRs under `docs/` still work.
4. Bump the baseline schema to version 2 and store `api` as
   `symbol -> normalized signature` instead of a name list. Version 1 files are
   still readable; signature comparison is skipped when a signature is absent.
   Removals and signature changes are reported.
5. Add a weighted `impact` score to `HistoryPoint` and rank "where it went bad"
   by impact, then finding count. Weights: cycles 10, API removal/dependency 4–5,
   edge 3, complexity 2, API growth/test inventory/atomic 1.

## Alternatives considered

- Keep first-parent only. Rejected: it hides most commits and all PR internals.
- Resolve full Python package exports through `__init__`. Deferred: requires
  cross-module import resolution; private-path filtering plus `__all__` removes
  the dominant noise now.
- Store API signatures only in history, not the baseline. Rejected: the baseline
  and history should share one metric definition.
- Keep raw-count ranking. Rejected: a 6,977-line merge with 484 findings ranked
  above a 4-line commit with 23 findings.

## Consequences

`--all` costs more than first-parent (roughly 2.5× commits for Click) but remains
parallel. Baseline files are larger because signatures are stored; v1 files still
load. The private-path heuristic applies to all languages, so an underscore-
prefixed JS/Rust module is treated as non-public. Merge commits still produce net
findings; `--all` adds the side-branch commits rather than replacing merge
analysis. Examples/docs no longer affect production metrics or gates.

## Invariants

- `--all` compares each commit to its first parent, not the previous list item.
- `--jobs` output equals `jobs=1` in both modes.
- `metric_excludes` never affects ADR detection (it is separate from `exclude`).
- Private paths never count as public API.
- Baseline v2 is deterministic and v1-readable.
- Impact ranking is stable and deterministic.

## Rollback

Remove `--all`, `metric_excludes`, private-path filtering, API signatures (restore
schema version 1), and impact ranking.
