# 0021. Resolve JS/TS tsconfig path aliases

- Status: accepted
- Date: 2026-11-27

## Context

The JS/TS adapter treated an import such as `@/util` as a package root unless a
`package.json` name matched. In TS monorepos this caused two opposite errors:

- `new-dependency` flagged a local alias as third-party;
- `package_edges` missed the import, so `forbidden-edge` and `import-cycle` could
  not see the dependency.

## Decision

Resolve aliases during JS/TS repo indexing:

- Scan `tsconfig.json` and `jsconfig.json` files, choosing the nearest config
  for each source file.
- Parse JSONC (comments and trailing commas) deterministically.
- Support local `extends` chains, `compilerOptions.baseUrl`, and
  `compilerOptions.paths`, including one `*` wildcard. Wildcard matches choose
  the longest literal prefix.
- Resolve target paths relative to the effective `baseUrl`. If the target maps
  to a repo path under a `package.json`, return that package name; if it maps to
  a repo path without a package, treat it as local and skip external dependency
  classification.
- Keep the public `imports()` result unchanged; an internal resolver is used by
  `package_edges` and `project_imports`.
- If no alias pattern matches, fall back to the previous module-root behavior.
- Package-manager `extends` entries and non-relative configs are not resolved;
  they fall back to the old behavior.

## Alternatives considered

- Require each alias target to exist on disk. Rejected: a valid alias may point
  to a not-yet-created local module; existence is not the guard's concern.
- Add a JSON5/TS parser dependency. Rejected: the needed JSONC subset is small;
  the guard must stay deterministic and avoid a new runtime dependency.
- Resolve aliases through `tsc`. Rejected: requires Node and a project build;
  the guard is AST/config-level.
- Change `imports()` to return full specifiers. Rejected: breaks the public
  adapter API and tests; an internal resolver keeps the change contained.

## Consequences

Local aliases no longer produce false new-dependency findings, and workspace
aliases participate in the package graph. Resolved aliases under the repository
are considered local even if the target path is absent. Unmatched aliases keep
the old package-root/external behavior. This is a JS/TS-only behavior change;
other adapters are unaffected.

## Invariants

- Nearest local config wins; nested configs do not inherit from unrelated roots.
- Comments, trailing commas, and escaped strings do not break parsing.
- A resolved alias never creates a `new-dependency` for a repository-local path.
- A resolved workspace alias can create a `forbidden-edge`/cycle edge.
- Same-package aliases do not create package edges.
- Unmatched imports preserve the pre-0021 behavior.
- No Node, `tsc`, or network invocation.

## Rollback

Remove tsconfig scanning and alias resolution, restore direct `imports()` use in
`package_edges`/`project_imports`, and delete this ADR.
