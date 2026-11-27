# Aedificatoria

Deterministic architecture guard for coding agents and humans.

`aed check` compares the working tree (including untracked files) against a base
commit in a detached worktree. It exits non-zero on mechanical violations. No
LLM calls, no network, no source mutation.

## Install and first run

Requires Python 3.11+ and a Git repository with at least one commit.

```sh
uv venv .venv
uv pip install -e '.[dev]'

.venv/bin/aed init       # aed.toml + docs/decisions/0001-*.md
.venv/bin/aed baseline   # aed-baseline.json
.venv/bin/aed check
.venv/bin/aed check --json
```

`aed check` never stages or commits.

| Exit | Verdict | Action |
|---:|---|---|
| `0` | `allow` | No findings. |
| `0` | `review` | Warnings only; read before merge. |
| `2` | `block` | Error(s); do not commit/merge. |

`2` also covers CLI, config, and git errors.

## Commands

| Command | Purpose |
|---|---|
| `aed check [--base REV] [--config PATH] [--json]` | Check working tree against a base. |
| `aed baseline` | Write the persistent metric baseline. |
| `aed history [--limit N] [--all] [--jobs N]` | Audit local history. |
| `aed history --github-pr N --repo OWNER/REPO` | Audit a GitHub PR via `gh`. |
| `aed history --gitlab-mr N --repo GROUP/PROJECT` | Audit a GitLab MR via `glab`. |
| `aed adr new TITLE [--status proposed]` | Create a numbered ADR. |
| `aed rules` | Print effective config and thresholds. |
| `aed init` | Create `aed.toml` and the first ADR. |

## Output contract

Text findings include severity, rule, path, message, and optional evidence.
`--json` returns:

```json
{"verdict":"block","base":"<sha>","changed":{"files":3,"added":42,"deleted":7},
 "counts":{"error":1,"warn":0,"info":0},
 "findings":[{"rule":"one-way-door","severity":"error","path":"migrations/42.sql"}]}
```

Agents should consume `verdict` and `findings`, not parse text. `path`, `line`,
and `evidence` are optional per finding.

## Rules

| Rule | Default | Trigger | Remedy |
|---|---|---|---|
| `one-way-door` | error | Change under migrations, schema, API, auth, `.proto`, or OpenAPI. | Accepted ADR. |
| `guard-config` | error | Effective `aed.toml` or baseline-file change. | Accepted ADR. |
| `change-amplification` | error | Diff exceeds the file/line budget. | Split it, or add an ADR. |
| `baseline` | error | Metric growth beyond caps, removed tests, assertion drop. | Fix it, or refresh with an ADR. |
| `test-tampering` | error | Deleted tests/assertions, new `skip`/`xfail`/`.only`. | Restore the test. |
| `test-config` | warn/error | Test configuration changed/deleted. | Restore; keep the verifier stable. |
| `import-cycle` | error | New top-level package cycle. | Remove the edge. |
| `forbidden-edge` | error | New `[[aed.forbidden_edges]]` edge. | Remove the edge. |
| `complexity` | error | Over `max_complexity`, or growth over `complexity_delta`. | Refactor. |
| `public-api` | error | Removed/renamed public symbol or changed signature. | Restore compatibility or add an accepted ADR. |
| `new-dependency` | error | New third-party import not allowlisted. | Reuse a dependency or record an allowlist decision. |
| `adr-quality` | warn | ADR missing title, status, or `## Decision`. | Fix the ADR. |

Private paths (`_module.py`, `_dir/`, except `__init__.py`) are excluded from
public API. `docs/**`, `examples/**`, and `benchmarks/**` are excluded from
production metrics by default. An accepted ADR needs a `# title`,
`- Status: accepted`, and a `## Decision`; create it with `aed adr new TITLE`.

## Configuration

Show effective values with `aed rules`. Minimal `aed.toml`:

```toml
[aed]
base = "HEAD"
adr_dir = "docs/decisions"
source_roots = ["src", "."]
metric_excludes = ["docs/**", "examples/**", "benchmarks/**"]
baseline_path = "aed-baseline.json"

[thresholds]
max_changed_files = 25
max_changed_lines = 800
max_complexity = 15
complexity_delta = 5
```

All keys, one-way-door overrides, test/API paths, excludes, and block flags:
[`aed.toml.example`](aed.toml.example). Baseline growth caps default to `0`.
Any effective config or baseline change requires an accepted ADR; `guard-config`
enforces this.

### Baseline

```sh
aed baseline   # commit aed-baseline.json
aed check
```

The baseline stores per-function complexity, public API signatures, external
dependencies, package edges, and per-test assertion counts. Growth is ratcheted
against it; removals and assertion drops are always errors. Renames look like a
removal plus an addition and require a refresh.

## History audit

```sh
aed history --limit 100 --jobs 8
aed history --all --limit 400 --jobs 8
aed history --github-pr 123 --repo owner/repo
```

Report-only: exit `0`, no writes. Each commit is measured in a temporary
worktree. First-parent is the default; `--all` walks every reachable commit and
compares it to its first parent. `--jobs` parallelizes local collection with
output identical to `--jobs 1`.

Merge commits are net diffs against the first parent; squashes are single atomic
commits; rebases are linear commits; octopus merges use the N-parent path.
Conflict-resolution edits are reported heuristically as `merge-only lines`.
Deep semantics, recovery, and caveats: [`docs/history.md`](docs/history.md).

## Agent and CI integration

```sh
aed check --json > /tmp/aed.json
```

If `verdict == "block"` (exit `2`), do not commit. `findings` gives the rule,
path, line, and evidence for the repair loop.

Enable the committed hook once per clone:

```sh
git config core.hooksPath .githooks
```

`--no-verify` bypasses the local hook; CI is authoritative. See
[`.github/workflows/aed.yml`](.github/workflows/aed.yml).

## Languages

| Language | Parser | Public API | Tests | Dependencies |
|---|---|---|---|---|
| Python | `ast` | top-level functions/classes, `__all__` | `test_*`, asserts, skip/xfail | absolute imports |
| Rust | tree-sitter | `pub` items, fields, variants, impl methods | `#[test]`, `assert!`, `#[ignore]` | `use` roots, Cargo tables |
| JS/TS | tree-sitter | ES/TS exports, CJS exports, methods | `test`/`it`/`describe`, `.skip`/`.only` | imports, `require`, `package.json` |

Adapters live in `languages.py`; rule code is language-neutral. Rust/JS/TS is
AST-level: no macro expansion, type checking, tsconfig aliases, or `.each`
tables. With cargo installed and a root `Cargo.toml`, `aed check` adds
`cargo-advisory` info findings from `cargo metadata --no-deps --offline`.

## Limitations

- Checks the working tree, not the staged index.
- Complexity is a branch-count heuristic.
- Cycles are package-level (Python top-level packages, Rust crates), not
  intra-package.
- Baseline keys use file plus symbol/function name; renames require a refresh.
- History uses current config, does not re-evaluate historical ADRs, and `--all`
  does not recover squashed internals.
- GitHub/GitLab fallback modes are opt-in, non-hermetic, and require `gh`/`glab`.
- ADR quality is structural. Semantic test weakening and cumulative drift are
  not fully detectable.

## Development

```sh
uv venv .venv
uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/aed check
```

Design decisions: [`docs/decisions/`](docs/decisions/). Dogfood the guard with:

```sh
git config core.hooksPath .githooks
aed check --base HEAD~1   # review the last commit
```
