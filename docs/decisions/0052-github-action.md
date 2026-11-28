# 0052. GitHub Action distribution and native report renderers

- Status: accepted
- Date: 2026-11-28

## Context

Using the guard in CI required copying a multi-step workflow: full-history
checkout, Python setup, package installation, and a shell resolver for
`pull_request.base.sha`, `merge_group.base_sha`, `push.before`, zero SHAs, and
force-pushes. `aed check` defaults `base = HEAD`, so a missing `--base` silently
produces an empty diff and passes. Findings existed only in process output:
warnings exited `0` by default, so the green check hid them, and there was no
annotation, job summary, or machine-readable artifact.

`actions/checkout` fetches one commit by default. The resolver needed a mapping
from event payload to a commit plus a targeted fetch; history is not required
because `base_worktree` uses `read-tree`/`checkout-index` and `changed_files`
uses `git diff`.

## Decision

Ship a root composite action (`action.yml`) that:

- Installs the PyPI release matching the action's own `pyproject.toml`, unless
  `version` is overridden. `version: source` installs the action checkout so
  unreleased refs can be dogfooded.
- Selects Python 3.11+ and calls `actions/setup-python` only when the runner's
  interpreter is older.
- Resolves the event base in an internal module (`aedificatoria._action`),
  fetching a missing commit with `--depth=1`. Force-push and new-branch cases
  fall back to a shallow default-branch fetch and `git merge-base`, deepening
  before failing. An unreachable explicit `base` is an error.
- Runs one analysis and renders multiple sinks: `--json-out PATH`, `--summary
  PATH`, and `--github-annotations`. `--json` remains stdout-only and is
  rejected together with annotations, which also write stdout.
- Exposes `verdict`, `exit-code`, `base`, counts, and the report path as step
  outputs. `enforce: false` reports without failing; tool errors still fail.
- Extends `aed init` with `--github` (write the pinned workflow) and
  `--baseline` (write the first baseline), so setup is one command.

GitHub annotations are capped at ten error/warning commands per step. The
renderer emits failures first, then up to ten notices, then a pointer to the
summary.

## Consequences

- CI needs only checkout and the action; `fetch-depth: 0` is no longer required.
- Warnings become visible without changing the default exit code.
- The action is coupled to the event base semantics but the CLI stays
  forge-neutral; `_action` is an internal module, not a stable CLI surface.
- Fork PRs get read-only tokens: annotations and summaries work, comment/upload
  features would not. None are part of v1.
- The action installs from PyPI, so a tag must be published before its workflow
  can run. `version: source` covers pre-release testing.
- Windows runners are untested for v1.
- `path:` does not scope the diff; monorepos still diff the whole repository.

## Alternatives considered

- JavaScript action. Rejected: a second runtime and bundle for a Python CLI.
- Docker action. Rejected for v1: image pull and git-in-container complexity;
  keep it for hermetic or air-gapped deployments.
- Reusable workflow. Rejected for v1: more YAML at the call site and less
  flexible than a composite action.
- Bash-only resolver. Rejected: event and fallback cases need tests; Python is
  already installed for the CLI.
- Read the config and baseline from the base branch. Deferred: it requires a
  policy/base split in `Context`; v1 keeps the existing guard-config warning.

## Invariants

- An unresolved base fails the step; it never falls back to `HEAD`.
- The action performs no writes to the repository, pushes no commits, and
  requires no token beyond `contents: read`.
- One `run_checks` pass produces every report; renderers never re-analyze.
- Explicit `--base` failures are not masked by the merge-base fallback.

## Rollback

Remove `action.yml`, the `aed init --github` template, and the workflow usage.
The `--json-out`, `--summary`, and `--github-annotations` flags are additive and
remain safe for other CI systems.
