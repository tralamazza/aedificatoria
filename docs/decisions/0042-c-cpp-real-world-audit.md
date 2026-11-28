# 0042. Pinned real-world C/C++ audit corpus

- Status: accepted
- Date: 2026-11-27

## Context

C/C++ was the only adapter with no real-world audit. Unit fixtures do not expose
template, macro, header, CMake, or include-graph behavior at repository scale.
Downloading repositories in CI would make the guard non-hermetic and heavy.

## Decision

Add `benchmarks/cfamily/` with:

- a pinned manifest of C and C++ repositories (`fmt`, `nlohmann/json`, `libuv`,
  `googletest`, `cJSON`);
- a runner that clones/fetches into an explicit cache, checks out pinned commits,
  runs `aed history --limit N --jobs N --json`, and records diagnostics/runtime;
- an `--offline` mode for cached audits;
- a report at `docs/c-cpp-audit.md`.

The corpus is diagnostic, not a CI gate: it requires network on first use and its
history findings are not labeled ground truth. Upstream source is not vendored.

## Consequences

C/C++ adapter regressions can be measured on real headers and histories without
adding network or large clones to normal CI. Runtime and finding volume are
recorded per repository. A future labeled mutation corpus can be derived from
observed false positives.

## Alternatives considered

- Vendor real repositories. Rejected: repository size, license/update burden.
- Clone in CI. Rejected: network, runtime, and nondeterminism.
- Use only synthetic fixtures. Rejected: misses real C++ templates/macros and
  CMake/include layouts.

## Rollback

Remove `benchmarks/cfamily/`, this ADR, and the audit report/link.
