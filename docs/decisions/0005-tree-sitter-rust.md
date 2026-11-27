# 0005. Retire the hand-written Rust lexer for pinned tree-sitter

- Status: accepted
- Date: 2026-11-27

## Context

ADR 0002 chose a dependency-free lexer for Rust because tree-sitter would add a
native parser dependency. ADR 0004 later made tree-sitter a core dependency for
JS/TS. The Rust lexer is about a thousand lines of bespoke tokenization and
structural analysis. It is item-level: struct fields and enum variants are not
compared, signatures are reconstructed from tokens, and new Rust syntax needs
manual lexer changes. It now duplicates the tree-sitter infrastructure rather
than avoiding a dependency.

## Decision

Use `tree-sitter-rust==0.24.2` for Rust, with the already-pinned
`tree-sitter==0.27.0` runtime. Rewrite `rust.py` around the syntax tree. Keep the
manifest/crate index independent of parsing: `Cargo.toml` scanning, crate names,
workspace edges, and declared dependencies are unchanged.

Behaviour stays equivalent for the guard's rules, with extra fidelity: public
struct fields, enum variants, and trait method signatures are now part of the
public API. Complexity uses the same branch concept (`if`, loops, `match` arms,
`&&`/`||`), excluding nested function bodies. Inline `#[test]` attributes,
`#[ignore]`, and `assert!`-family macros are read from attributes and macro nodes.

## Alternatives considered

- Keep the lexer. Rejected: lower fidelity, duplicate parsing infrastructure,
  and manual work for every Rust syntax addition.
- Make the Rust grammar an optional extra. Rejected: absent grammars would
  silently skip Rust files and make the verdict environment-dependent.
- Use rust-analyzer, `cargo check`, or `rustc` output. Rejected: needs a
  toolchain, builds the project, and is expensive on work-in-progress trees.

## Consequences

Core installs gain one grammar wheel. Analysis is deterministic for the pinned
version, offline, and needs no Cargo toolchain. The public API surface is more
complete than the lexer's. Macros are still not expanded, and the dependency
graph remains crate-level. Python and JS/TS behavior is unchanged.

## Invariants

- `tree-sitter` and every grammar stay exactly pinned.
- Analysis never invokes Cargo, rustc, or the network.
- Rust files are never silently skipped when the adapter is installed.
- Existing tests for public API, complexity, test tampering, imports, crate
  cycles, dependencies, and local modules must remain green.

## Rollback

Restore `rust.py` from the commit before this ADR, remove the
`tree-sitter-rust` dependency and allowlist entry, and record a superseding ADR.
