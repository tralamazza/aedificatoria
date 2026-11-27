# Aedificatoria

**Architecture guard for coding agents: make design constraints executable.**

Coding agents are confident. They rarely report uncertainty, and architectural
mistakes do not fail the build until much later. Aedificatoria does not ask the
model how it feels; it inspects the diff and fails the change when it crosses a
one-way door, weakens the verifier, adds a cycle, ratchets complexity, breaks a
public API without a decision record, or introduces an unapproved dependency.

It is not an agent and not an LLM judge. It is the deterministic guardrail an
agent calls before it commits.

## Quick start

```sh
uv venv .venv
uv pip install -e '.[dev]'

.venv/bin/aed init
.venv/bin/aed baseline
.venv/bin/aed check
.venv/bin/aed check --json
```

`aed check` compares the working tree to a base commit (`HEAD` by default),
creates a detached worktree of the base, and runs every check against both
trees. Exit code `2` means block; `0` means allow or review.

## What it checks

| Rule | What it catches |
|---|---|
| `one-way-door` | Changes under migrations, schema, API, auth, `.proto`, OpenAPI require an ADR with a title, status, and `## Decision`. |
| `test-tampering` | Deleted tests, per-test or total assertion drops, new `skip`/`xfail`/`.only`, or deleted test configuration. |
| `guard-config` | An effective `aed.toml` change (thresholds, allowlists, excludes, paths, doors, baseline) without an ADR. |
| `baseline` | Growth beyond the committed metric baseline: per-function/total complexity, public API, dependencies, package edges, removed tests, assertion drops. |
| `import-cycle` | New top-level package cycles. |
| `forbidden-edge` | New dependency edges listed in `[[aed.forbidden_edges]]`. |
| `complexity` | A function crosses the complexity limit, or increases by more than the ratchet delta. |
| `public-api` | Removed public symbols or changed signatures without an ADR. |
| `new-dependency` | A new third-party import that is not allowlisted. |
| `change-amplification` | The diff grows past the file/line budget; an ADR downgrades it to a warning. |
| `adr-quality` | An ADR is deleted, lacks a title/status, or lacks a `## Decision`. |

## Languages

| Language | Parser | Public API | Tests | Dependencies |
|---|---|---|---|---|
| Python (`.py`) | `ast` | top-level functions, classes, `__all__` | `test_*` functions, asserts, skip/xfail | absolute imports |
| Rust (`.rs`) | pinned tree-sitter Rust grammar | `pub` fn/struct/enum/trait/type/const/static, public fields and variants, impl and trait methods | `#[test]`/`#[tokio::test]`, inline `#[cfg(test)]` modules, `assert!` macros, `#[ignore]` | `use` roots and Cargo dependency tables |
| JavaScript/TypeScript (`.js .jsx .mjs .cjs .ts .tsx .mts .cts`) | pinned tree-sitter (JS, TS, TSX grammars) | ES exports, TS interfaces/types/enums, class methods, CJS exports | `test`/`it`/`describe`, `expect`/`assert`, `.skip`/`.todo`/`.only` | imports/re-exports/`require` + `package.json` dependency tables |

Adapters live in `languages.py`; each one exposes parsing, public API,
complexity, test information, package edges, imports, and local packages.
Checks dispatch by file extension, so adding a language means adding an
adapter module and one registry entry, not editing the rules.

Rust and JavaScript/TypeScript use exact-pinned tree-sitter grammars: no
toolchain and no network at analysis time, but native parser wheels. Rust
remains item-level for macros (no expansion), but its public API now includes
public struct fields and enum variants.

### Optional cargo advisory

When `cargo` is installed and the repository has a root `Cargo.toml`, `aed check`
also runs `cargo metadata --no-deps --offline` against the base and head trees.
It adds `cargo-advisory` findings with severity `info`:

- crate cycles declared in manifests but absent from the base;
- new declared dependencies the manifest scan missed, or scanner-only
  dependencies cargo does not attach to a package.

Advisory findings never change the verdict, so a machine without cargo produces
the same exit code and the same blocking findings. The command runs offline,
resolves no dependencies, and writes no lockfile or target directory. Disable it
with `cargo_advisory = false`.

## Configuration

`aed init` writes `aed.toml`:

```toml
[aed]
base = "HEAD"
adr_dir = "docs/decisions"
source_roots = ["src", "."]
allow_new_dependencies = []
metric_excludes = ["docs/**", "examples/**", "benchmarks/**"]
baseline_path = "aed-baseline.json"
cargo_advisory = true
require_adr_for_large_change = true
block_removed_tests = true
block_removed_asserts = true
block_new_skips = true
block_breaking_api = true

[thresholds]
max_changed_files = 25
max_changed_lines = 800
max_complexity = 15
complexity_delta = 5
max_baseline_complexity_growth = 0
max_baseline_api_growth = 0
max_baseline_dependency_growth = 0
max_baseline_edge_growth = 0

# [[aed.forbidden_edges]]
# from = "api"
# to = "db"
# reason = "API must go through the service layer."
```

One-way-door paths, test paths, API paths, and excludes are configurable; see
[`aed.toml.example`](aed.toml.example). The configuration is the verifier's
control plane: any effective change to `aed.toml` requires an ADR in the same
diff, so a one-line threshold, allowlist, exclude, or door change cannot silently
disable a check.

### Persistent baseline

`aed baseline` writes a deterministic snapshot of per-file/function complexity,
public API symbols with signatures, external dependencies, package edges, and
per-test assertion counts to `aed-baseline.json`. `aed check` compares the working
tree against that snapshot, not only against the immediate base. Small increments
therefore accumulate against the baseline instead of resetting every commit.
Public API ignores private paths, and `metric_excludes` keeps docs/examples out of
production metrics; same-name signature changes are reported.

The four growth caps default to `0`: no net complexity, API, dependency, or edge
growth without a deliberate refresh. Set positive caps for planned expansion.
Removing a baseline public symbol or test, or dropping a test's assertion count,
is always an error.

Refreshing the baseline is itself a verifier change: `guard-config` requires an
ADR for any change to the baseline file. The workflow is:

```sh
aed baseline
# add or update an ADR explaining the accepted growth
aed check
```

A missing baseline is an `info` finding so adoption never blocks; a malformed
baseline is an error. Keys are file paths plus symbol/function names, so a rename
looks like a removal plus an addition and needs a refresh.

### History audit

For an existing repository, `aed history` reconstructs the last first-parent
commits and ranks them by metric regressions:

```sh
aed history --limit 30
aed history --limit 100 --jobs 8
aed history --all --limit 400 --jobs 8
aed history --since v1.0.0 --json
```

Each commit is checked out into a temporary worktree and measured with the same
metrics as the baseline. Consecutive commits are compared with zero growth caps,
so the report surfaces per-function and total complexity growth, API removals and
signature changes, new dependencies, new package edges, new cycles, removed tests,
and assertion drops. It prints a timeline and a ranked "where it went bad"
section; `--json` returns the same data. It is a report, not a gate: exit 0, no
writes.

`--all` walks every reachable commit and compares it to its **first parent**
instead of walking only the first-parent chain. Side-branch commits then appear
individually; merge commits remain net diffs. Public API ignores private paths
(`_module.py`, `_dir/`), and `metric_excludes` removes docs/examples/benchmarks
from production metrics. The baseline stores API signatures, so same-name
signature breaks are reported. "Where it went bad" is ranked by a weighted
`impact` score (cycles/API/dependencies above test inventory), not raw count.

Metric collection is independent per commit. `--jobs N` runs it in process
workers with isolated worktrees and preserves order; the output is identical to
`--jobs 1`. On `pallets/click`, a 100-commit window dropped from 27s to about 4–6s
with 8 workers. The default is 1; keep `N` at or below CPU cores, and expect one
parser tree per worker. Forge API modes (`--github-pr`, `--gitlab-mr`) do not use
`--jobs`.

The timeline also shows each commit's kind and churn. A single-parent commit whose
subject ends in `(#N)` is treated as a squashed merge. If a single-parent commit
exceeds `max_changed_files` or `max_changed_lines`, history warns that it is a
large atomic change and its internal commits are unavailable.

A multi-parent merge is **not** atomic: its branch commits remain in history. A
large merge net diff still produces a `large merge` warning and points to
`--all`; use that (or the PR/MR history mode) when the merge internals matter.
First-parent history otherwise sees only the merge's net diff.

Conflict-resolution edits are a separate case: they exist on neither parent.
History runs `git show --cc` and reports files with lines added or removed
relative to **every** parent as `merge-only lines ... (potential conflict
resolution)`. Same-file auto-merges that only combine parent lines are not
flagged. This is a report signal, not proof that a conflict existed.

Octopus merges use the same N-parent path: every parent branch is reachable with
`--all`, the merge remains a net diff against its first parent, and the combined
diff uses one prefix column per parent. Clean octopus merges produce no merge-only
finding; manual octopus commits with conflict-resolution edits are inspected.

For squash-merge workflows, run history and the guard on the PR branch before
merging:

```sh
aed history --since origin/main
aed check --base origin/main
```

Squashing permanently discards the intermediate commits from `main`. For
architecture-sensitive work, prefer a merge or rebase merge so the commits remain
analyzable.

#### Recovering a squashed PR

The branch may be gone, but hosting providers often keep pull-request refs after
deletion. GitHub exposes `refs/pull/<N>/head`; fetch it yourself (aed never touches
the network), then point history at that tip:

```sh
git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'
aed history --head refs/remotes/origin/pr/123 --since origin/main
```

`--head` accepts any ref or commit, so this also works for a preserved branch,
a merge-queue ref, or a commit recovered from `git fsck`. If the host has purged
the PR refs, the intermediate commits may still exist as PR data.

#### GitHub API fallback

When even the PR refs are gone, GitHub may still retain the PR commits. The
opt-in mode reconstructs them through the GitHub CLI:

```sh
gh auth login          # once; or export GH_TOKEN
aed history --github-pr 123 --repo owner/repo --limit 20
```

It uses `gh api` to list the PR commits (`GET /pulls/{n}/commits`, capped by
GitHub at 250), downloads a source tarball per commit, and reads commit stats for
churn. Local commit objects are used when available. Authentication and
pagination are handled by `gh`; `GH_HOST` selects GitHub Enterprise. Automation
needs `gh` authenticated (`GH_TOKEN`), including for public repositories.
Results are report-only and never part of `aed check`. The first commit in the
analyzed window is the baseline; branch-level growth is still audited by
`aed check --base` before merge.

This is inherently non-hermetic: it needs `gh`, network access, credentials, and
GitHub's retention of the PR objects. A purged commit returns 404 and the
internals are unrecoverable. Persist `aed history --json` at merge time when a
durable audit is required.

#### GitLab API fallback

GitLab keeps MR refs as well:

```sh
git fetch origin '+refs/merge-requests/*/head:refs/remotes/origin/merge-requests/*'
aed history --head refs/remotes/origin/merge-requests/123/head --since origin/main
```

If those refs are purged, the opt-in GitLab mode uses `glab`:

```sh
glab auth login        # once; or export GITLAB_TOKEN
aed history --gitlab-mr 123 --repo group/subgroup/repo --limit 20
```

It uses `glab api` to list MR commits, downloads a source archive per commit,
and reads commit stats plus the diff list for churn. Project paths may contain
subgroups and are URL-encoded for the API. `glab` handles auth, pagination,
`GITLAB_TOKEN`/`OAUTH_TOKEN`, and `GITLAB_HOST`. Like the GitHub mode, it is
report-only, non-hermetic, never runs without the flag, and is never used by
`aed check`. A missing project or purged commit fails with the API error.

Limits: first-parent only; the current configuration is used for every commit;
historical ADRs, one-way doors, and config weakening are not re-evaluated; cost
grows with `--limit` and repository size.

## Agent integration

Call the guard before applying or committing a change:

```sh
aed check --json > /tmp/aed.json
```

Parse `verdict`. `block` means exit `2` and do not commit. The `findings` array
contains `rule`, `severity`, `path`, `line`, and `evidence`, so the agent can
feed the exact violation back into its repair loop.

A pre-commit hook is committed at
[`.githooks/pre-commit`](.githooks/pre-commit). Enable it once per clone:

```sh
git config core.hooksPath .githooks
```

It checks the whole working tree, so use it when the whole uncommitted diff is
the change. Hidden tests should still live outside the agent's writable tree.

## Why these rules

- **Base worktree, not chat history.** The guard compares the candidate working
  tree to a clean worktree of the base commit. No model memory, no context
  compaction.
- **Package-level graph.** Architectural cycles are top-level package cycles,
  not every import edge. Relative imports stay inside their package, so only
  absolute cross-package imports become edges.
- **ADRs as the decision record.** A one-way-door change without a written
  decision fails. The record captures alternatives, invariants, and rollback,
  and survives context loss.
- **The verifier is the reward model.** Tests are not allowed to be deleted,
  weakened, or skipped to make a change pass. Test configuration is watched.
- **Ratchet, not absolute perfection.** Complexity is checked as an increase
  over the base as well as against an absolute limit, so existing debt does not
  block unrelated changes.
- **Allowlists over trust.** New dependencies are explicit decisions, not
  incidental additions.

## Limitations

- Python, Rust, JavaScript, and TypeScript are supported. The Python parser is
  `ast`; Rust and JS/TS use pinned tree-sitter grammars.
- Rust results are AST-level: macros are not expanded, trait-impl methods
  without `pub` are not tracked, and the dependency graph is crate-level. JS/TS
  results are AST-level, but no type checking is performed: tsconfig path
  aliases are skipped, `.each` test tables are not tracked, and unbound arrows
  or object-literal methods are not named in the complexity ratchet.
- Complexity is a heuristic (branch count), not a full cognitive-complexity
  model.
- The dependency graph is package-level (Python top-level packages, Rust
  crates); module-level cycles inside one package or crate are not reported.
- `aed check` compares the working tree to a base commit, not the staged index.
  A staged-only mode is planned.
- The baseline keys metrics by file plus symbol/function name. Renames look like
  removals plus additions and need a baseline refresh; cumulative file/line churn
  is still judged per diff.
- History audits are first-parent by default, use the current configuration for
  every commit, and do not re-evaluate historical ADRs or one-way doors. `--all`
  adds side-branch commits but merge commits are still first-parent net diffs.
  Merge-only conflict-resolution lines are a heuristic from `git show --cc`;
  pure reorders, choosing one parent exactly, and some binary conflicts are not
  detected. Octopus merges are treated as N-parent merges: first parent is the
  mainline net, `--all` walks every branch, and merge-only detection uses one
  prefix column per parent. Squash merges are atomic: history detects PR-style
  squashes and flags oversized ones, but the intermediate commits are
  unrecoverable. Check the PR branch before squashing.
- `--jobs` parallelizes local metric collection only; each worker holds a parser
  tree and a temporary worktree. Forge API modes remain sequential.
- The GitHub/GitLab API fallbacks are opt-in and non-hermetic: they need
  `gh`/`glab` authenticated, network, and host retention. The GitHub PR commit
  list is capped at 250. They never affect `aed check`.
- The guard is per-diff. An ADR-authorized configuration weakening still exits 0,
  replacing an assertion with a weaker one while keeping the count is semantic
  and not detected, and cumulative drift across many small commits is handled
  only for the baseline metrics listed above.
- The guard is deterministic. It does not judge whether an ADR is *good*, only
  whether a decision was recorded and the mechanical constraints hold.

## Development

```sh
uv venv .venv
uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

### Dogfooding

Aedificatoria guards its own diffs. Enable the committed hook once per clone:

```sh
git config core.hooksPath .githooks
```

Every commit then runs `aed check` against `HEAD`, and a violating change is
blocked before it lands.

A clean working tree compares against `HEAD` and is a no-op. To review what the
last commit introduced, run:

```sh
.venv/bin/aed check --base HEAD~1
```

The guard does not run from a hook when no `.venv` or `aed` binary is available;
it prints a skip notice instead.

CI runs the same guard on every push and pull request via
[`.github/workflows/aed.yml`](.github/workflows/aed.yml). It uses the PR base
SHA, or the pushed-from commit, as `--base`, so the gate cannot be bypassed with
`--no-verify`.

## Roadmap

- tsconfig path-alias resolution and `.each` test tables.
- Module-level cycle detection and per-module forbidden edges.
- Staged-index mode for real pre-commit hooks.
- Mutation-score ratchet on changed modules.
- Change-amplification measured per feature/task, not only per diff.
- MCP/tool wrapper so agents can call the guard directly.
