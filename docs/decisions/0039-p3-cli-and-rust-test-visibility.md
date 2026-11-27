# 0039. P3 CLI and Rust test visibility fixes

- Status: accepted
- Date: 2026-11-27

## Context

Rust comments between `#[test]` and a function cleared pending attributes, so
tests were invisible to test inventory and test-tampering checks. CLI commands
`init`, `adr new`, and `rules` used the supplied path instead of the repository
root, so a config/ADR created from a subdirectory could be ignored by `check`.
`aed rules` also omitted effective exclusions, paths, allowlists, and block flags.

## Decision

- Ignore Rust `line_comment`/`block_comment` nodes without clearing pending
  attributes.
- Resolve `init`, `adr new`, and `rules` to the Git repository root when the
  supplied path is inside a repository; fall back to the project directory
  outside Git.
- Print effective excludes, test/test-config/API paths, metric excludes, allowed
  dependencies, and block flags from `aed rules`.

## Consequences

Rust tests separated from their attributes by comments are inventoried correctly.
All policy-consuming commands operate on the same root as `check`, `baseline`,
and `history`. `aed rules` now exposes the full effective policy, at the cost of
longer output on projects with many patterns.

## Alternatives considered

- Require callers to run all commands from the repository root. Rejected:
  subdirectory use is common and already normalized by check/history.
- Keep comments as attribute-resetting children. Rejected: comments are not Rust
  attributes and must not change test discovery.

## Rollback

Restore path-local CLI behavior and attribute resetting on comments; remove the
additional `aed rules` output and tests.
