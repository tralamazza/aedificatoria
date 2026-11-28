# Aedificatoria

Deterministic architecture guard for coding agents and humans.

`aed check` compares the working tree (including untracked files) against a base
commit materialized into a temporary tree. `--staged` compares the index instead.
It exits `2` on errors and `0` on warnings unless strict mode is enabled. No LLM
calls, no network, no source mutation.

## Install

Requires Python 3.11+ and Git.

```sh
uv tool install aedificatoria
# or
pipx install aedificatoria

aed doctor
```

For a one-off run without a persistent install:

```sh
uvx --from aedificatoria aed check --staged
# or
pipx run --spec aedificatoria aed check --staged
```

In a project virtualenv, `pip install aedificatoria` also works. Then start with:

```sh
aed init       # aed.toml + docs/decisions/0001-*.md
aed baseline   # aed-baseline.json
aed check
aed check --json
```

## Install from source

```sh
uv venv .venv
uv pip install -e '.[dev]'

.venv/bin/aed doctor
.venv/bin/aed init
.venv/bin/aed baseline
.venv/bin/aed check
```

`aed check` never stages or commits.

## Usefulness evaluation

The mutation benchmark and agent A/B protocol live in
[`benchmarks/usefulness`](benchmarks/usefulness/README.md). CI runs the labeled
mutation corpus with a recall/false-block gate; real-world history audits remain
separate diagnostic evidence.

## Tool diagnostics

```sh
aed doctor
aed doctor --json
```

Required: Python 3.11+, `git`, and the pinned tree-sitter runtime/grammars.
Optional: `cargo` for advisory metadata, `gh` for GitHub PR audits, and `glab`
for GitLab MR audits. Optional tools are reported but do not fail the command.
Exit `1` when a required tool is missing or Python is older than 3.11. The
command is local, read-only, and makes no network calls.

`aed check` exit codes:

| Exit | Verdict | Action |
|---:|---|---|
| `0` | `allow` | No errors or warnings; info findings may be present. |
| `0` | `review` | Warnings only; read before merge. |
| `1` | `review` + strict | Warnings fail under `--strict` or `fail_on_warn = true`. |
| `2` | `block` | Error(s); do not commit/merge. |

`2` also covers CLI, config, and git errors.

## Commands

| Command | Purpose |
|---|---|
| `aed check [--base REV] [--config PATH] [--staged] [--strict] [--json] [--json-out PATH] [--summary PATH] [--github-annotations]` | Check working tree or staged index against a base. |
| `aed doctor [--json]` | Check required and optional installed tools. |
| `aed baseline` | Write the persistent metric baseline. |
| `aed history [--limit N] [--all] [--jobs N]` | Audit local history. |
| `aed history --github-pr N --repo OWNER/REPO` | Audit a GitHub PR via `gh`. |
| `aed history --gitlab-mr N --repo GROUP/PROJECT` | Audit a GitLab MR via `glab`. |
| `aed adr new TITLE [--status proposed]` | Create a numbered ADR. |
| `aed rules` | Print effective config and thresholds. |
| `aed init [--github] [--baseline]` | Create `aed.toml` and the first ADR; optionally the GitHub workflow and baseline. |

## Output contract

Text findings include severity, rule, path, message, and optional evidence.
`--json` returns:

```json
{"tool":"aedificatoria","verdict":"block","base":"<sha>",
 "changed":{"files":3,"added":42,"deleted":7},
 "counts":{"error":1,"warn":0,"info":0},
 "findings":[{"rule":"one-way-door","severity":"error",
              "message":"one-way-door change without an accepted architecture decision record: migrations/42.sql",
              "path":"migrations/42.sql"}]}
```

Agents should consume `verdict` and `findings`, not parse text. `path`, `line`,
and `evidence` are optional per finding.

`--json-out PATH` writes the same payload to a file without changing stdout.
`--summary PATH` appends a Markdown report (use `$GITHUB_STEP_SUMMARY`), and
`--github-annotations` emits GitHub Actions workflow commands, worst findings
first. `--json` and `--github-annotations` cannot be combined because both use
stdout.

## Rules

| Rule | Default | Trigger | Remedy |
|---|---|---|---|
| `one-way-door` | error | Change under migrations, schema, API, auth, `.proto`, or OpenAPI. | Accepted ADR. |
| `guard-config` | error | Effective `aed.toml` or baseline-file change. | Accepted ADR. |
| `change-amplification` | error | Diff exceeds the file/line budget. | Split it, or add an ADR. |
| `baseline` | error | Metric growth beyond caps, removed/changed public symbols, removed tests, assertion/case drop. | Restore compatibility/tests; refresh metric caps with an accepted ADR. |
| `test-tampering` | error | Deleted tests/assertions, new `skip`/`xfail`/`.only`. | Restore the test. |
| `syntax-error` | error | A changed Python/Rust/JS/TS/C/C++ file newly fails to parse (base-invalid files ignored). | Fix the syntax error. |
| `test-config` | warn/error | Test configuration changed/deleted. | Restore; keep the verifier stable. |
| `import-cycle` | error | New package-level cycle. | Remove the edge. |
| `forbidden-edge` | error | New `[[aed.forbidden_edges]]` edge. | Remove the edge. |
| `complexity` | error | New function over `max_complexity`, or existing function growth over `complexity_delta`. | Refactor. |
| `public-api` | error | Removed/renamed public symbol or changed signature, including field type/width. | Restore compatibility or add an accepted ADR. |
| `abi` | error | Public record layout, field order/addition, packing, enum underlying type, or virtual order changed. | Restore layout compatibility or add an accepted ADR. |
| `new-dependency` | error | New third-party import not allowlisted. | Reuse a dependency or record an allowlist decision. |
| `adr-quality` | warn | ADR missing title/status/`## Decision`, or deleted. | Fix or supersede the ADR. |
| `cargo-advisory` | info | Cargo metadata disagrees with the manifest scan, or reports a new crate cycle. | Review; informational only. |

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
section. Under the default block flags, only accepted ADRs authorize
one-way-door, verifier-config, baseline, and public-API/ABI changes; a proposed
ADR does not authorize them and the change remains a finding. `adr-quality`
checks only structure, not whether the decision itself is good.

`aed baseline` and `aed adr new` reject absolute, parent-traversing, or escaping
config paths and symlinked outputs; `aed init` rejects symlinked targets. Source
scanners honor Git ignores and skip symlinked or oversized files. Accepted ADRs
are a human review gate; use
CODEOWNERS and branch protection for `aed.toml`, `aed-baseline.json`,
`docs/decisions/**`, hooks, and workflows.

## Configuration

Show effective values with `aed rules`. Minimal `aed.toml`:

```toml
[aed]
base = "HEAD"
adr_dir = "docs/decisions"
source_roots = ["src", "."]
metric_excludes = ["docs/**", "examples/**", "benchmarks/**"]
baseline_path = "aed-baseline.json"
fail_on_warn = false

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

Findings are report-only (exit `0`) and do not modify the checkout. Each commit
is measured in a temporary tree. First-parent is the default; `--all` walks the
reachable graph up to `--limit` and compares each commit to its first parent.
`--jobs` parallelizes local collection with output identical to `--jobs 1`.

Merge commits are net diffs against the first parent; squashes are single atomic
commits; rebases are linear commits; octopus merges inspect every parent when
detecting merge-only lines. Conflict-resolution edits are reported heuristically
as `merge-only lines`.
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
- `review` / exit `0` (default; `fail_on_warn = true` makes it `1`): stop for
  human approval; do not auto-commit.
- `allow`: commit.
- Run `aed check --staged --json` immediately before committing to check the
  exact index.
- Exit `2` also covers CLI/git/config errors, so parse the JSON rather than
  treating every failure as a finding.

### GitHub Actions

```yaml
permissions:
  contents: read
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: tralamazza/aedificatoria@v0.1.7
```

The action resolves the base from the event (`pull_request.base.sha`,
`merge_group.base_sha`, or `push.before`), fetching it when absent with a
shallow merge-base fallback, and fails closed when no base resolves.
`fetch-depth: 0` is not required. It writes a job summary, inline annotations,
and `aed.json`, and exposes `verdict`, `exit-code`, and counts as outputs. Use
`strict: true` to fail on warnings and `enforce: false` for shadow mode. Full
reference: [`docs/github-action.md`](docs/github-action.md).

`aed init --github --baseline` writes this workflow pinned to the installed
release and creates the first baseline.

### Manual CI invocation

Any CI system can run the CLI directly:

```yaml
- run: aed check --base "$BASE" --json-out aed.json --summary "$GITHUB_STEP_SUMMARY"
```

- `--base` must point at a commit that exists in the checkout. `aed check`
  defaults to `HEAD`, which is an empty diff in a clean checkout; resolve the
  event base yourself or fetch it (`git fetch --depth=1 origin "$BASE"` is
  enough).
- If the event base is missing or the zero SHA, resolve
  `git merge-base HEAD origin/main`; `--base HEAD~1` is the last-resort fallback
  when history is available.
- CI uses working-tree mode. `--staged` is for pre-commit.
- `review` exits `0` by default. Set `fail_on_warn = true` in `[aed]` or pass
  `aed check --strict` to exit `1` for warnings; errors still exit `2`.
  As an output-only alternative: `jq -e '.verdict == "allow"' aed.json >/dev/null`.
- Upload `aed.json` as an artifact even on failure.

### Pre-commit and trust boundary

```sh
git config core.hooksPath .githooks
```

The hook runs `aed check --staged` via `AED_BIN`, PATH, or the workspace
`.venv`, and skips with a warning when none is available; `--no-verify` can
bypass it locally, so CI is authoritative.

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
| C/C++ | tree-sitter C/C++ | header functions/templates/classes/enums/typedefs, concepts, public members, overload sets, exported modules/partitions | GoogleTest/Catch2 macros, assertions, skips | local quoted includes, external angled headers |

See the [C/C++ real-world audit](docs/c-cpp-audit.md) for current depth and gaps.

Adapters live in `languages.py`; rule code is language-neutral. Rust/JS/TS is
AST-level: no macro expansion or type checking. JS/TS resolves nearest
`tsconfig.json`/`jsconfig.json` `baseUrl`/`paths` aliases, including local
`extends`; package-manager `extends` and unresolved aliases fall back to external
classification. `test.each`/`it.each` literal and template tables contribute
case counts; variable tables are recognized but not ratcheted. C/C++ API comes
from headers and module interface units; `.h` tries C++ then C when parse errors
are lower. Includes use quoted-local/angled-external classification, with C/C++
standard headers allowed; template parameters, overload sets, concepts, module
partitions, and `export import` edges are compared structurally; CMake
targets/dependencies and macro expansion are not resolved; ABI checks are
structural, not compiled. With cargo installed, a root `Cargo.toml`, and
`cargo_advisory = true` (the default), `aed check` adds `cargo-advisory` info
findings from `cargo metadata --no-deps --offline`.

## Limitations

- Default checks use the working tree; use `--staged` (or the hook) to check the
  index.
- Complexity is a branch-count heuristic.
- Cycles are package-level (Python top-level packages, Rust crates, JS/TS npm
  packages, C/C++ directory components), not intra-package.
- Baseline keys use file plus symbol/function name; renames require a refresh.
- History uses current config, does not re-evaluate historical ADRs, and `--all`
  does not recover squashed internals.
- GitHub/GitLab fallback modes are opt-in, non-hermetic, and require `gh`/`glab`.
- ADR quality is structural. Semantic test weakening and cumulative drift are
  not fully detectable.
- C/C++ uses header and module-interface declarations as API. Template
  parameters, overload sets, concepts, and module partition/export-import
  identity are structural; modules expose only their exported surface and
  partition contents are not merged.
  ABI checks cover record field order, virtual order, enum underlying type,
  packing attributes, and field addition; field type/width changes report as
  `public-api`. They are target-independent and do not resolve calling
  conventions, name mangling, target layout, or macro visibility.
  Macros are not expanded, ABI is not compiled, and CMake targets/dependencies
  are not parsed; package edges use directory components and local quoted
  includes.

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
aed check --base HEAD~1   # diff the working tree against the previous commit
```

## License

MIT. See [`LICENSE`](LICENSE).
