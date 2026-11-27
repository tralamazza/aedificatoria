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
| `test-tampering` | Deleted tests, removed assertions, new `skip`/`xfail`, or deleted test configuration. |
| `import-cycle` | New top-level package cycles. |
| `forbidden-edge` | New dependency edges listed in `[[aed.forbidden_edges]]`. |
| `complexity` | A function crosses the complexity limit, or increases by more than the ratchet delta. |
| `public-api` | Removed public symbols or changed signatures without an ADR. |
| `new-dependency` | A new third-party import that is not allowlisted. |
| `change-amplification` | The diff grows past the file/line budget; an ADR downgrades it to a warning. |
| `adr-quality` | An ADR is deleted, lacks a title/status, or lacks a `## Decision`. |

## Configuration

`aed init` writes `aed.toml`:

```toml
[aed]
base = "HEAD"
adr_dir = "docs/decisions"
source_roots = ["src", "."]
allow_new_dependencies = []
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

# [[aed.forbidden_edges]]
# from = "api"
# to = "db"
# reason = "API must go through the service layer."
```

One-way-door paths, test paths, API paths, and excludes are configurable; see
[`aed.toml.example`](aed.toml.example).

## Agent integration

Call the guard before applying or committing a change:

```sh
aed check --json > /tmp/aed.json
```

Parse `verdict`. `block` means exit `2` and do not commit. The `findings` array
contains `rule`, `severity`, `path`, `line`, and `evidence`, so the agent can
feed the exact violation back into its repair loop.

A pre-commit variant is in [`examples/pre-commit`](examples/pre-commit). It
checks the working tree, so use it when the whole uncommitted diff is the
change. Hidden tests should still live outside the agent's writable tree.

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

- Python-only in this version. The parser and complexity metric are Python AST
  based; TypeScript/JavaScript adapters are the next step.
- Complexity is a heuristic (branch count), not a full cognitive-complexity
  model.
- The dependency graph is package-level; module-level cycles inside one
  top-level package are not reported.
- `aed check` compares the working tree to a base commit, not the staged index.
  A staged-only mode is planned.
- The guard is deterministic. It does not judge whether an ADR is *good*, only
  whether a decision was recorded and the mechanical constraints hold.

## Development

```sh
uv venv .venv
uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

## Roadmap

- TypeScript/JavaScript adapter (tree-sitter or `dependency-cruiser`).
- Module-level cycle detection and per-module forbidden edges.
- Staged-index mode for real pre-commit hooks.
- Mutation-score ratchet on changed modules.
- Change-amplification measured per feature/task, not only per diff.
- MCP/tool wrapper so agents can call the guard directly.
