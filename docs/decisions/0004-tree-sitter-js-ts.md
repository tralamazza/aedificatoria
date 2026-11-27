# 0004. JavaScript and TypeScript analysis with pinned tree-sitter

- Status: accepted
- Date: 2026-11-27

## Context

Aedificatoria must guard JS/TS diffs: exports, import graphs between npm
workspace packages, declared dependencies, test tampering, and complexity. ADR
0002 rejected tree-sitter and chose a dependency-free lexer for Rust. JS/TS is
different: regex/division ambiguity, template literals, JSX, TypeScript type
annotations, arrow functions, and re-export barrels make a token-level analyzer
unreliable. The guard's output must be trustworthy, and the verdict must not
depend on a Node toolchain or network access.

## Decision

Use in-process tree-sitter with exact pins as a core dependency:

- `tree-sitter==0.27.0`
- `tree-sitter-javascript==0.25.0`
- `tree-sitter-typescript==0.23.2`

The adapter lives in `jsts.py` and parses per extension: JS/JSX with the
JavaScript grammar, TS/MTS/CTS with the TypeScript grammar, TSX with the TSX
grammar. `parse_text` now takes the path so every adapter can choose a grammar;
Python and Rust ignore it.

The adapter covers top-level ES exports, TS declarations, class methods, CJS
exports, static imports/re-exports/`require`/dynamic `import`, npm package
boundaries via the nearest `package.json`, dependency tables, `test`/`it`/
`describe` (including skips and focused tests), assertions via `expect`/`assert`,
and branch-count complexity for declarations, methods, and bound arrows.

## Alternatives considered

- A dependency-free JS/TS lexer. Rejected: the ambiguity above produces false
  positives and negatives, which is worse than not analyzing the file.
- `tsc`, `eslint`, or `dependency-cruiser` subprocesses. Rejected: requires
  Node and a resolvable project, writes artifacts, and reports lint thresholds
  rather than base-to-head deltas.
- An optional `aed[js]` extra. Rejected: an absent parser would silently skip
  JS/TS files and make the verdict environment-dependent. The parser is core so
  JS/TS analysis either works or the install fails loudly.

## Consequences

Core installs now include native parser wheels. Parse results are deterministic
for the pinned versions; upgrading a grammar is a deliberate change that must be
covered by the adapter tests. No Node, package manager, or network call is
needed at analysis time. Tree-sitter produces partial trees for invalid input,
so a work-in-progress file is analyzed where it parses instead of failing the
run. Rust remains a dependency-free lexer.

## Invariants

- The three parser packages stay exactly pinned.
- Analysis never invokes Node, npm, or the network.
- JS/TS files are never silently skipped when the adapter is installed.
- `parse_text` is path-aware so grammar selection follows the extension.
- Existing Python and Rust results must not change.

## Rollback

Remove the adapter and its registry entry, drop the three dependencies, restore
`parse_text(text)` for Python and Rust, and revert to ADR 0002's dependency-free
policy.
