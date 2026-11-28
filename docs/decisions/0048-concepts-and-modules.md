# 0048. Concept and module interface API coverage

- Status: accepted
- Date: 2026-11-28

## Context

C++20 concepts were parsed by the pinned tree-sitter grammar but not emitted as
API, so removing or changing a public concept was invisible. The grammar has no
module or export grammar, so `.ixx`/`.cppm`/`.mpp` interfaces parse with errors
and non-exported declarations could be mistaken for public API.

## Decision

- Emit `concept_definition` nodes as public API. The identity includes template
  parameters and the normalized concept definition with comments removed.
- Add `.cppm` and `.mpp` to C/C++ headers, API paths, and module extensions.
- Detect module interface units and extract only top-level `export` declarations
  and exported blocks into a synthetic translation unit for API parsing.
- Normalize module/export/import syntax for syntax checking while preserving line
  numbers, so valid new modules do not produce `syntax-error` findings.
- Keep non-exported module declarations out of public API.

Add nine mutation cases: concept removal, requirement change, concept addition
(clean), concept comment (clean), module export removal, module export signature
change, module export addition (clean), internal module change (clean), and a
new module interface (clean).

The benchmark now contains 41 cases: 27 positive and 14 clean.

## Consequences

Concept and exported-module API changes are visible. Module support is
export-surface only; it does not evaluate module partitions, module linkage, or
template instantiation. The extractor is deliberately line/lexical for module
scaffolding, while declaration parsing remains tree-sitter.

## Alternatives considered

- Wait for module grammar support. Rejected: leaves valid modules as syntax
  errors and API-blind.
- Treat all module declarations as public. Rejected: internal implementation
  changes would create false API breaks.

## Rollback

Remove the nine cases, module extensions/path entries, concept extraction, and
module normalization; restore normal parsing for module files.
