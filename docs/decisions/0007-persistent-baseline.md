# 0007. Persistent metric baseline and cumulative caps

- Status: accepted
- Date: 2026-11-27
- Amended by: 0015 (API signatures, metric scope, baseline v2); 0022 (test cases, v3)

> Later: schema v2 stores public API names with signatures and excludes private
> and metric-excluded files per ADR 0015. Schema v3 adds parameterized test-case
> counts per ADR 0022. The ratchet design is unchanged.

## Context

Every check compared the working tree to an immediate base. A change that would
fail as one diff could be split into many passing commits: file/line budgets
reset each time, complexity could grow by the per-diff delta repeatedly, public
API additions were free, and test changes could be laundered. The guard had no
memory, so architectural decay was a time-series problem with a per-commit
answer.

## Decision

Commit a metric baseline (`aed-baseline.json` by default) and compare the
working tree against it on every check. The baseline records:

- per-file, per-function complexity;
- per-file public API symbols with signatures (schema v2 per ADR 0015);
- external dependencies;
- package dependency edges;
- per-file, per-test assertion counts.

`aed baseline` writes the snapshot deterministically. `aed check` fails on:

- any existing function whose complexity exceeds its baseline value;
- total complexity above baseline plus `max_baseline_complexity_growth`;
- any baseline public symbol that disappeared;
- public API count above baseline plus `max_baseline_api_growth`;
- more new dependencies than `max_baseline_dependency_growth`;
- more new package edges than `max_baseline_edge_growth`;
- any baseline test that disappeared, or any test whose assertion count dropped.

All four growth caps default to `0`: no net growth without a baseline refresh.
The baseline path and caps are configuration, so changing them is already a
`guard-config` ADR decision. Changing the baseline file itself is included in
`guard-config`; refreshing it is a deliberate, ADR-visible action. A missing
baseline is an `info` finding, not a block, so adoption is possible; malformed
baselines are errors.

Adopting the baseline in this repository is recorded by this ADR; the snapshot is committed alongside it.

Metric keys are file paths plus symbol/function names. Renames therefore look
like removals plus additions: removals are errors for public symbols and tests,
while new functions are bounded by the absolute complexity limit and the total
cap. Refreshing the baseline after a rename is the documented path.

## Alternatives considered

- Compare against `origin/main` or a merge-base only. Rejected: it still resets
  after every merge and has no memory of the previous worst state.
- Store metrics in git notes or an external database. Rejected: not reviewable
  with the code and not available offline in every checkout.
- Make every growth an unconditional error with no refresh path. Rejected:
  legitimate work needs an explicit, reviewed reset.
- Cap only total complexity. Rejected: per-function regressions and public API,
  dependency, edge, and test erosion need their own ratchets.

## Consequences

The default policy is strict: any net complexity or API growth, new dependency,
new package edge, removed test, or per-test assertion drop requires refreshing
the baseline with an ADR. Teams can set positive growth caps for planned
expansion. Baseline commits are larger and must be regenerated after accepted
changes. Cumulative line/file churn is still judged per diff; only the listed
metrics are cumulative.

## Invariants

- Baseline output is deterministic (sorted keys, sorted lists, no timestamps).
- `check_baseline` never consults the base worktree; it compares head metrics to
  the committed baseline.
- A missing baseline never blocks; a malformed one always does.
- The baseline file is part of `guard-config`; changing it requires an ADR.
- Python, Rust, and JS/TS all feed complexity, API, dependency, edge, and test
  metrics.

## Rollback

Delete `baseline.py`, remove `check_baseline`, the `baseline` command, the
baseline configuration fields, and `aed-baseline.json`.
