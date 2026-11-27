# Aedificatoria

Deterministic architecture guard for coding agents and humans.

`aed check` compares the working tree (including untracked files) against a base
commit materialized into a temporary tree. `--staged` compares the index instead.
It exits non-zero on mechanical violations. No LLM calls, no network, no source
mutation.

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
| `aed check [--base REV] [--config PATH] [--staged] [--json]` | Check working tree or staged index against a base. |
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
| `complexity` | error | New function over `max_complexity`, or existing function growth over `complexity_delta`. | Refactor. |
| `public-api` | error | Removed/renamed public symbol or changed signature. | Restore compatibility or add an accepted ADR. |
| `new-dependency` | error | New third-party import not allowlisted. | Reuse a dependency or record an allowlist decision. |
| `adr-quality` | warn | ADR missing title, status, or `## Decision`. | Fix the ADR. |

Private paths (`_module.py`, `_dir/`) are excluded from public API unless a
symbol is explicitly re-exported through a package `__init__.py`. `docs/**`,
`examples/**`, and `benchmarks/**` are excluded from production metrics by
default.

## ADRs

**ADR** means **Architecture Decision Record**: a numbered Markdown file under
`adr_dir` (default `docs/decisions/`) that records the context, decision,
alternatives, consequences, invariants, and rollback path for a consequential
change. Create one with `aed adr new TITLE`.

An accepted ADR needs a `# title`, `- Status: accepted`, and a `## Decision`
section. Accepted ADRs authorize one-way-door, verifier-config, baseline, and
public-API changes; `adr-quality` checks only that structure, not whether the
decision itself is good.

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
Policy config and baseline changes require an accepted ADR; `guard-config`
enforces this. `cargo_advisory` is the one excluded toggle because it cannot
affect the verdict.

### Baseline

```sh
aed baseline   # commit aed-baseline.json
aed check
```

The baseline stores per-function complexity, public API signatures (including
resolved Python `__init__.py` re-exports), external dependencies, package edges,
per-test assertion counts, and parameterized test-case counts. Growth is
ratcheted against it; removals and assertion/case drops are always errors.
Renames look like a removal plus an addition and require a refresh.

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

### Agent harness

Apply the candidate patch, then run the guard before commit:

```python
import json
import subprocess

def run_guard(repo: str, base: str | None = None, staged: bool = False):
    cmd = ["aed", "check", "--path", repo, "--json"]
    if base:
        cmd += ["--base", base]
    if staged:
        cmd.append("--staged")

    proc = subprocess.run(cmd, text=True, capture_output=True)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(proc.stderr or "aed produced no JSON")

    if result["verdict"] == "block":
        return "block", result["findings"]
    if result["verdict"] == "review":
        return "human", result["findings"]
    return "allow", []
```

Harness policy:

- `block` / exit `2`: feed `rule`, `path`, `line`, and `message` back into the
  repair loop; cap retries, then fail.
- `review` / exit `0`: stop for human approval; do not auto-commit.
- `allow`: commit.
- Run `aed check --staged --json` immediately before committing to check the
  exact index.
- Exit `2` also covers CLI/git/config errors, so parse the JSON rather than
  treating every failure as a finding.

### CI

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: actions/setup-python@v5
  with:
    python-version: "3.11"
- run: pip install -e .
- name: Guard
  env:
    BASE: ${{ github.event.pull_request.base.sha || github.event.before }}
  run: aed check --base "$BASE" --json > aed.json
```

- `fetch-depth: 0` is required so `--base` is resolvable.
- This repository installs the guard with `pip install -e .`. In another project,
  replace that line with a pinned `aedificatoria` package/commit install.
- If the event base is missing or the zero SHA, resolve
  `git merge-base HEAD origin/main`; the repository workflow does this and the
  HEAD~1 fallback already: [`.github/workflows/aed.yml`](.github/workflows/aed.yml).
- CI uses working-tree mode. `--staged` is for pre-commit.
- `review` exits `0`; to fail warnings too:
  `jq -e '.verdict == "allow"' aed.json >/dev/null`.
- Upload `aed.json` as an artifact even on failure.

### Pre-commit and trust boundary

```sh
git config core.hooksPath .githooks
```

The hook runs `aed check --staged`; `--no-verify` can bypass it locally, so CI
is authoritative.

The guard is not a sandbox. Protect `aed.toml`, `aed-baseline.json`,
`docs/decisions/**`, `.githooks/pre-commit`, and `.github/workflows/aed.yml`
with `CODEOWNERS`/branch protection. `guard-config` detects verifier edits, but
an accepted ADR authorizes them; make ADR acceptance a human gate. Install `aed`
from a pinned version outside the agent-writable workspace when possible.

## Languages

| Language | Parser | Public API | Tests | Dependencies |
|---|---|---|---|---|
| Python | `ast` | top-level functions/classes, `__all__`, `__init__` re-exports | `test_*`, asserts, skip/xfail | absolute imports |
| Rust | tree-sitter | `pub` items, fields, variants, impl methods | `#[test]`, `assert!`, `#[ignore]` | `use` roots, Cargo tables |
| JS/TS | tree-sitter | ES/TS exports, CJS exports, methods | `test`/`it`/`describe`, `.skip`/`.only`, `.each` case tables | imports, `require`, `package.json` |
| C/C++ | tree-sitter C/C++ | header functions/classes/enums/typedefs, public members | GoogleTest/Catch2 macros, assertions, skips | local quoted includes, external angled headers |

Adapters live in `languages.py`; rule code is language-neutral. Rust/JS/TS is
AST-level: no macro expansion or type checking. JS/TS resolves nearest
`tsconfig.json`/`jsconfig.json` `baseUrl`/`paths` aliases, including local
`extends`; package-manager `extends` and unresolved aliases fall back to external
classification. `test.each`/`it.each` literal and template tables contribute
case counts; variable tables are recognized but not ratcheted. C/C++ API is
header-only; `.h` tries C++ then C when parse errors are lower. C/C++ includes
use quoted-local/angled-external classification, with C/C++ standard headers
allowed; CMake targets/dependencies, macros, templates, and ABI are not
resolved. With cargo installed and a root `Cargo.toml`, `aed check` adds
`cargo-advisory` info findings from `cargo metadata --no-deps --offline`.

## Limitations

- Default checks use the working tree; use `--staged` (or the hook) to check the
  index.
- Complexity is a branch-count heuristic.
- Cycles are package-level (Python top-level packages, Rust crates), not
  intra-package.
- Baseline keys use file plus symbol/function name; renames require a refresh.
- History uses current config, does not re-evaluate historical ADRs, and `--all`
  does not recover squashed internals.
- GitHub/GitLab fallback modes are opt-in, non-hermetic, and require `gh`/`glab`.
- ADR quality is structural. Semantic test weakening and cumulative drift are
  not fully detectable.
- C/C++ uses header declarations as API. Macros/templates are not expanded,
  ABI is not checked, and CMake targets/dependencies are not parsed; package
  edges use directory components and local quoted includes.

## Development

```sh
uv venv .venv
uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/aed check
```

Design decisions: [`docs/decisions/`](docs/decisions/). Real-world audit notes:
[`docs/real-world-audit.md`](docs/real-world-audit.md). Dogfood the guard with:

```sh
git config core.hooksPath .githooks
aed check --base HEAD~1   # review the last commit
```

## License

MIT. See [`LICENSE`](LICENSE).
