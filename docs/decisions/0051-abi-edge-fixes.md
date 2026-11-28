# 0051. ABI edge-case fixes for typedefs and non-layout edits

- Status: accepted
- Date: 2026-11-28

## Context

Review of the structural ABI check found four issues:

- `typedef struct/enum` records were invisible, including anonymous typedefs.
- Field fingerprints included default member initializers and non-layout
  attributes, causing false ABI errors.
- Anonymous-namespace records were treated as public ABI.
- Field-width changes produced both `public-api` and `abi` findings.

## Decision

- Descend into `type_definition` in the ABI surface. Anonymous typedef records
  use the typedef alias as their ABI key; named records keep their tag.
- Build field fingerprints from type, declarators, bitfields, and layout-relevant
  attributes only. Ignore default initializers, `[[deprecated]]`, `mutable`, and
  other non-layout decorations. Preserve `alignas`, `packed`, and `aligned`.
- Strip non-layout attributes and `final` from record headers; preserve
  layout-relevant attributes.
- Skip anonymous namespaces in ABI extraction.
- Keep typedef API identity structural (`typedef:<record kind>`) so layout
  changes are owned by `abi`, not duplicated by `public-api`.
- Suppress an `abi` finding when `public-api` already reports the same file's
  source change; field-width changes are now owned by `public-api`.

Add four regression cases: anonymous typedef field reorder, default member
initializer (clean), deprecation attribute (clean), and anonymous-namespace
record change (clean). The benchmark now contains 59 cases: 37 positive and 22
clean. ABI-specific positive cases remain six.

## Consequences

Typedef ABI is now visible and common non-layout edits no longer false-positive.
The ABI surface remains structural: target layout, calling convention, mangling,
macro-expanded visibility, and actual by-value use are unresolved. Suppressing
`abi` when any public API change exists in the same file can hide an ABI-only
change in that file; the public API finding still blocks.

## Alternatives considered

- Keep full typedef text in public API. Rejected: every layout edit becomes a
  duplicate `public-api` finding and `abi` never owns typedef layout.
- Keep reporting both `public-api` and `abi`. Rejected: noisy for one change.

## Rollback

Remove the four cases, restore the previous typedef/field/namespace ABI
semantics, and restore duplicate reporting.
