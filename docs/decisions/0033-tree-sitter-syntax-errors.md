# 0033. Syntax errors for tree-sitter adapters

- Status: accepted
- Date: 2026-11-27

## Context

ADR 0032 added `syntax-error` for Python because `ast.parse` returns `None` on
invalid input. Rust, JavaScript, TypeScript, and C/C++ use tree-sitter, which
returns a partial tree even for malformed input. Downstream checks therefore
analyze incomplete syntax and can report misleading API, test, or complexity
results without any parse-error finding.

## Decision

Extend `syntax-error` to every tree-sitter adapter:

- Python keeps using `ast.parse` errors.
- Rust, JS/TS, and C/C++ use `root_node.has_error`.
- The first `ERROR` or missing node supplies the line; the source line is
  evidence.
- Compare base and head with the same logic as Python: only a syntax error
  introduced by the change is reported.
- Empty and pre-existing invalid files remain ignored.

C/C++ `.h` files use the existing C++/C fallback before error extraction, so the
chosen grammar's error state is reported.

## Consequences

Malformed Rust, JS/TS, and C/C++ changes are visible instead of silently
analyzed as partial trees. The pinned grammars define what counts as a syntax
error; grammar lag can create a finding, so pins and fixtures remain part of the
compatibility surface. The guard still does not type-check or compile.

## Alternatives considered

- Keep syntax-error Python-only. Rejected: the other adapters have the same
  silent-partial-tree failure mode.
- Reject any file where `has_error` is true in all checks. Rejected: diagnostics
  belong in one finding; existing checks should keep their established behavior.
- Use compilers or language servers. Rejected: non-hermetic and toolchain-
  dependent.

## Rollback

Remove the tree-sitter syntax-error helpers, adapter dispatch, tests, README
row, and this ADR, and restore Python-only syntax-error behavior.
