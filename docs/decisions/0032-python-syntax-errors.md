# 0032. Report new Python syntax errors

- Status: accepted
- Date: 2026-11-27
- Amended by: 0033 (tree-sitter adapters)

## Context

The Python adapter returns `None` from `ast.parse` when a file has a syntax
error. Downstream checks skip that file, so a syntax error can hide API, test,
or dependency changes from the guard until the verifier fails. Empty Python
files are valid and must not be reported.

The guard should report syntax errors introduced by the change, not pre-existing
parse failures that are already in the base tree.

## Decision

Add a `syntax-error` check for Python files:

- Skip deleted files, excluded paths, and non-Python adapters.
- Parse the head file. If it parses, no finding.
- Parse the base file at `base_path`. If the base was already invalid, no finding.
- Otherwise report `syntax-error` with `error` severity, the parser line, and the
  offending source line as evidence.

Empty strings now parse as an empty `ast.Module` instead of returning `None`.
`syntax_error(text)` is a separate pyast helper so callers can distinguish
"valid empty file" from "unparsable file".

## Consequences

Invalid Python is visible in `findings` instead of silently skipped. The check
operates on the same working-tree/base-tree inputs as the other checks and needs
no new dependency. Pre-existing invalid files remain skipped so a repository can
adopt the guard without an unrelated cleanup.

## Alternatives considered

- Let tree-sitter parse partial Python trees. Rejected: the guard would analyze
  structurally broken code and could report misleading API/test results.
- Report every invalid file, including pre-existing failures. Rejected: unrelated
  debt would block every change to the repository.
- Treat empty files as invalid. Rejected: empty Python files are valid.

## Rollback

Remove `check_syntax_errors`, its `run_checks` entry, the `syntax_error` helper,
tests, and this ADR.
