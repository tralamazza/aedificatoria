# 0050. Structural ABI mutation coverage

- Status: accepted
- Date: 2026-11-28

## Context

The guard compared source API declarations but reported ABI as unresolved.
Layout changes such as field reordering, virtual method reordering, enum
underlying changes, packing attributes, and field additions were invisible.
Without labeled cases, ABI coverage could not be claimed.

## Decision

- Add a C/C++ `abi_surface` over API headers:
  - public record header including bases and attributes;
  - non-static data fields in declaration order;
  - virtual methods in declaration order with signatures;
  - enum headers including underlying type.
- Add an `abi` check. Removed records and changed fingerprints are errors unless
  an accepted ADR justifies them; `block_breaking_api = false` downgrades to
  info.
- Ignore non-virtual private method bodies and comments.
- Add eight mutation cases: field reorder, enum underlying change, virtual order
  change, packing change, field addition, field width change, private method body
  (clean), and record comment (clean).

The benchmark now contains 55 cases: 36 positive and 19 clean.

## Consequences

ABI checks are target-independent structural checks, not a compiled layout
oracle. They intentionally err toward flagging layout changes in public headers.
They do not resolve calling conventions, symbol mangling, target sizes/alignment,
macro-expanded visibility, module records, or actual by-value use. Field-width
changes may also produce a `public-api` finding.

## Alternatives considered

- Use a compiler to dump layouts. Rejected for the default path: target and
  toolchain dependent, slower, and not hermetic across agents.
- Do not add ABI cases. Rejected: leaves a consumer-visible regression class
  invisible.

## Rollback

Remove the `abi` check, `abi_surface`, eight cases, and restore the previous
baseline.
