# 0002. Language adapters for non-Python repositories

- Status: accepted
- Date: 2026-11-27
- Amended by: 0004 (JS/TS), 0005 (Rust tree-sitter)

> Later: Rust now uses pinned tree-sitter per ADR 0005. This record documents
> the adapter architecture; the dependency-free Rust lexer is historical.

## Context

The guard's checks were written against a Python AST: imports, public API,
complexity, and test tampering all called `pyast` directly. Supporting Rust
means the same deterministic checks must run over a different syntax tree
without forking every check or adding an LLM judge.

## Decision

Introduce `languages.py`, a registry of adapters with a fixed surface:
`parse_text`, `public_api`, `complexity`, `test_info`, `is_stdlib`,
`package_edges`, `project_imports`, and `local_packages`. Checks select an
adapter by file extension and never import a language module directly.

Rust support initially used a dependency-free lexer plus brace matcher in
`rust.py`. It analysed `use`/`extern crate` imports, `pub` items and impl/trait
methods, branch-count complexity, and inline `#[cfg(test)]` tests including
`assert!` macros and `#[ignore]`. Package-level edges were crates, discovered
from `Cargo.toml` manifests; Cargo dependency tables fed the new-dependency
allowlist. Shared `ImportRef` and `TestInfo` types moved to `model.py` so
language modules do not depend on each other.

## Alternatives considered

- A tree-sitter dependency per language. Rejected: adds grammar wheels and a
  native dependency to a deliberately dependency-free guard.
- Calling `cargo metadata` or `cargo test --no-run`. Rejected: requires a
  toolchain and network, and is slower than reading the diff.
- Keeping Python and Rust checks separate. Rejected: duplicates every rule and
  lets the two implementations drift.

## Consequences

Adding a language is one adapter module and one registry entry. The checks stay
mechanical and language-independent. Under the original Rust lexer, analysis was
item-level and lexical: struct fields and enum variants were not compared, macros
were not expanded, and crate-level edges are coarser than module-level ones.
ADRs 0004/0005 replaced that lexer with tree-sitter for Rust and JS/TS.

## Invariants

- Checks never import `pyast` or `rust` directly; they use `languages.get`.
- Adapter analysis stays deterministic. The original dependency-free policy was
  superseded for Rust/JS/TS by ADRs 0004/0005.
- Python behaviour is unchanged; the default API and test paths add Rust files.
- Rust test modules inside production files are watched for deleted or skipped
  tests and dropped assertions.

## Rollback

Delete the Rust adapter and its registry entry; checks fall back to Python-only
behaviour. Shared types may move back to `pyast.py`.
