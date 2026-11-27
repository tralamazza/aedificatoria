# 0037. P2 adapter and dependency-scope fixes

- Status: accepted
- Date: 2026-11-27

## Context

A follow-up review found false API surfaces in Rust and JS/TS, repeated Python
re-export resolution failures, non-ASCII/dependency-scope leakage, and a
nameless `package.json` losing its dependency identity.

## Decision

- Share a fresh resolution path for each sibling Python re-export.
- Treat only public Rust traits and impls on public types as API; private impl
  methods no longer enter the public surface.
- Strip comments from named JS/TS declaration signatures.
- Apply exclusion and metric-exclusion filters to C/C++, Python, Rust, and JS/TS
  dependency/edge scans.
- Preserve dependencies and directory identity for nameless `package.json`
  manifests.
- Invalidate extended JS/TS configs on any `tsconfig*`/`jsconfig*` change.
- Error when an explicitly requested `--config` file is missing.
- Document that disabling `require_adr_for_large_change` leaves an over-budget
  change as an error even with an ADR.

## Consequences

Workspace crate identities remain local even when only manifests exist. Private
Rust implementation details and JS/TS comments no longer create false API
breaks. Dependency scans now honor exclusions consistently. The guard's public
API signatures are unchanged; only internal cleanup and dependency facts differ.

## Alternatives considered

- Treat every impl method as API. Rejected: private types are not callable
  through the public surface.
- Keep manifest dependencies unfiltered. Rejected: vendored/test manifests can
  suppress real dependency findings.
- Rename `require_adr_for_large_change`. Deferred to avoid a config migration;
  the behavior is now documented.

## Rollback

Restore the previous Rust/JS API collectors, Python re-export sharing, dependency
filters, package loader, and config validation; remove the added tests and ADR.
