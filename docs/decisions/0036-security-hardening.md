# 0036. Security hardening and P1 correctness fixes

- Status: accepted
- Date: 2026-11-27

## Context

A security and correctness review found workspace escapes and false negatives:
`aed baseline` and `aed adr new` could write outside the repository through
attacker-controlled paths or symlinks; proposed ADRs could downgrade one-way and
API findings; source scans followed symlinks and ignored `.gitignore`; renames and
BOM-prefixed Python files missed metrics; incremental history did not invalidate
re-exported package API surfaces.

## Decision

- Confine baseline and ADR/init paths to the repository, reject absolute paths,
  parent traversal, and symlink targets; write baselines atomically.
- Require an accepted ADR for authorizing checks. Proposed ADRs produce errors;
  `aed adr new` no longer offers `accepted` as a CLI status (accepted ADRs are a
  human review gate).
- Enumerate source files through Git when available, honoring `.gitignore` and
  submodules. Reject symlinked source files and cap individual source reads.
- Parse rename numstat with `-z`, fixing non-ASCII and directory-rename churn.
- Strip a leading BOM before Python parsing.
- Match one-way doors against both the new and base paths.
- Recompute Python package `__init__.py` API surfaces during incremental history
  updates.
- Use a bounded glob matcher, reject symlinked configs, cap config/baseline
  sizes, catch recursion as a CLI error, and sanitize terminal output.
- Use a user-private lock directory with `O_NOFOLLOW`; pin the PyPI publish action
  to a commit; make pre-commit prefer `AED_BIN`/PATH over a workspace binary.
- Protect `aed.toml`, the baseline, ADRs, hooks, and workflows with CODEOWNERS.

## Consequences

Symlinked or oversized source files are skipped rather than followed. Existing
ADR workflows must accept ADRs through human review before they authorize a
change. Incremental history does slightly more work for Python packages.
Malformed config/baseline input returns exit 2 rather than a traceback. Existing
repositories with symlinked source files may report fewer metrics.

## Alternatives considered

- Follow symlinks inside the repository. Rejected: a Git symlink can point outside
  and bypass the repository boundary.
- Keep proposed ADRs as warnings. Rejected: ordinary CI treats warnings as exit 0.
- Require every source read to use `Context._safe_join`. Rejected as insufficient:
  baseline/history scans do not have a changed-file context and need a shared
  bounded reader.
- Pin every action and build dependency. Deferred; the publish action is pinned
  because it receives the OIDC credential.

## Rollback

Restore the prior path handling, source enumeration, ADR status semantics, rename
and BOM parsing, incremental update behavior, and review only the ADR/docs/tests.
