# 0003. Optional cargo metadata as an advisory pass

- Status: accepted
- Date: 2026-11-27

## Context

The Rust analyzer reads manifests lexically so `aed check` stays hermetic and
can analyse a base commit without a toolchain. Cargo already knows the exact
workspace members, declared dependencies, and declared crate edges. Running it
is optional: it may be absent, may fail on an in-progress manifest, and its
output can vary by version.

## Decision

When `cargo` is installed and the repository has a root `Cargo.toml`, run
`cargo metadata --no-deps --offline` for the base worktree and the working tree.
Emit findings under the `cargo-advisory` rule with severity `info` only:

- crate cycles declared in manifests and absent from the base;
- declared dependencies cargo sees that the manifest scan missed;
- declared dependencies the scan reports that cargo does not attach to a
  package.

Info severity never changes the verdict. Without cargo, the output is identical
minus advisory lines. Cargo runs offline, resolves no dependencies, and writes
no lockfile or target directory. `cargo_advisory = false` disables the pass.

## Alternatives considered

- Make cargo authoritative for Rust dependency facts. Rejected: the verdict
  would then depend on whether cargo is installed and which version is present.
- Run `cargo clippy` as part of the check. Rejected: clippy needs a compiling
  tree, compiles dependency graphs twice for base and head, can write lockfiles
  and target directories, and reports lint thresholds rather than base-to-head
  deltas.
- Do nothing. Rejected: misread manifest forms and manifest-declared crate
  cycles are useful second opinions, and cargo metadata is a 20 ms read-only
  call.

## Consequences

Agents and CI see authoritative manifest facts when cargo is available, while
the blocking verdict stays deterministic and toolchain-free. Advisory findings
may vary between machines; that is explicitly outside the verdict.

## Invariants

- `cargo-advisory` findings are always `info`.
- Cargo is invoked only with `--no-deps --offline`.
- Base and head advisory runs use the same command and configuration.
- The deterministic checks never consume cargo output.

## Rollback

Delete `cargo.py`, remove `check_cargo_advisory` from `run_checks`, and drop the
`cargo_advisory` configuration key.
