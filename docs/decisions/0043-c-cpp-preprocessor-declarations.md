# 0043. C/C++ declarations inside preprocessor conditionals

- Status: accepted
- Date: 2026-11-27

## Context

The C/C++ adapter ignored `preproc_ifdef`, `preproc_if`, `preproc_else`, and
`preproc_elif` container nodes. A normal include-guarded header therefore
produced zero public API symbols, even when its declarations were otherwise
parseable. The C/C++ real-world audit exposed this on cJSON.

## Decision

Recurse into preprocessor conditional containers for:

- public API extraction;
- complexity function collection;
- GoogleTest/Catch2 test inventory.

Collect the union of declarations across branches. The grammar does not evaluate
preprocessor conditions; the guard intentionally treats all branches as possible
build configurations.

## Consequences

Include-guarded headers and export-macro declarations now contribute API,
complexity, and test metrics. cJSON moved from 0 to 105 public API symbols at the
audited tip. Historical findings increased because previously invisible code is
now analyzed.

## Alternatives considered

- Evaluate preprocessor conditions. Rejected: requires a compiler/config matrix
  and makes results environment-dependent.
- Only handle `#ifndef` include guards. Rejected: the same AST wrapper is used by
  all conditional declarations; a general traversal is simpler and safer.
- Implement export-macro unwrapping first. Rejected: the macro was nested in the
  ignored conditional; fixing the container was the root cause.

## Rollback

Remove preprocessor container recursion from API, complexity, and test walkers,
and remove the regression test and audit report update.
