# 0049. Module partition and export-import linkage identity

- Status: accepted
- Date: 2026-11-28

## Context

Module extraction covered exported declarations but ignored the module partition
name and `export import` edges. Renaming `math:part`, removing
`export import :part`, or changing the re-exported partition could therefore pass
without a public API finding. Private module fragments also needed normalization
so valid modules did not become syntax errors.

## Decision

- Add a `module:<name>` API key for each exported module interface, including
  partition names such as `math:part`.
- Add an `export-import:<target>` API key for every top-level `export import`
  edge. Removals and target changes break API; additions are allowed.
- Ignore plain `import` edges; changing an internal partition import stays clean.
- Normalize `module :private;` before parsing; private-fragment changes stay
  outside API.
- Add six mutation cases: partition rename, export-import removal, export-import
  target change, export-import addition (clean), internal import change (clean),
  and private-fragment change (clean).

The benchmark now contains 47 cases: 30 positive and 17 clean.

## Consequences

Partition identity and re-export linkage are visible to the guard. The extractor
still does not merge declarations from imported partitions, resolve partition
linkage across units, or model header-unit imports semantically. Module keys are
file-local API identity.

## Alternatives considered

- Treat only exported declarations as API. Rejected: partition and re-export
  changes are consumer-visible.
- Follow exports across partition files. Rejected: no hermetic cross-file module
  resolver and it would duplicate package-level graph concerns.

## Rollback

Remove the module identity/export-import keys, private-fragment normalization, and
the six cases.
