# 0034. Tool diagnostics command

- Status: accepted
- Date: 2026-11-27

## Context

Aedificatoria depends on Python 3.11+, `git`, and pinned tree-sitter packages.
It optionally uses `cargo` for advisory metadata, `gh` for GitHub PR history
audits, and `glab` for GitLab MR audits. Before this change, a missing required
runtime package could make `aed` fail during import, with no preflight command
to explain the environment problem.

## Decision

Add `aed doctor`:

- Required: Python 3.11+, `git`, and every pinned tree-sitter runtime/grammar.
- Optional: `cargo`, `gh`, and `glab`; missing optional tools are reported but
  do not fail the command.
- Exit `1` when a required tool is missing or incompatible; exit `0` otherwise.
- Support `--json` with per-tool `name`, `kind`, `required`, `installed`,
  `version`, `detail`, and `ok` fields.
- Keep the command local, read-only, and network-free.
- Import command-specific dependencies lazily so `doctor` can report a missing
  required tree-sitter package instead of crashing while loading the CLI.

## Consequences

Operators and agents get one deterministic place to diagnose setup issues.
The tool list is now duplicated between `doctor.py` and `pyproject.toml`; both
must stay in sync when dependencies change. Version checks remain availability
checks, not compatibility checks beyond the Python minimum.

## Alternatives considered

- Run diagnostics only inside `aed check`. Rejected: a missing parser prevents
  `check` from loading and obscures the cause.
- Make `gh`/`glab` required. Rejected: they support opt-in network history modes.
- Check authentication as well. Rejected: doctor remains local and read-only;
  `gh auth login`/`glab auth login` are user-managed.

## Rollback

Remove `aed doctor`, `doctor.py`, its tests and README section, and restore eager
CLI imports; the rest of the guard is unchanged.
