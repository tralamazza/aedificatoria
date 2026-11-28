# 0047. Structural C++ template and overload API identity

- Status: accepted
- Date: 2026-11-28

## Context

C++ template declarations were partially invisible: template classes were not
emitted as API, template parameter lists were omitted from function signatures,
and overloaded functions overwrote each other under one symbol name. The
usefulness corpus could not distinguish an added overload from a removed or
changed one.

## Decision

- Extract class/struct/union/enum declarations nested under
  `template_declaration` as aggregate API.
- Include the normalized template parameter list in aggregate and function API
  values.
- Merge same-name function signatures into a deterministic overload set encoded
  in the API value.
- Treat a base overload set as compatible when it is a subset of the head set;
  additions stay allowed, while removals and signature changes report
  `public-api`.

Add seven mutation cases: template function removal, template parameter change,
template class field removal, overload addition (clean), overload removal,
overload signature change, and a template comment-only change (clean).

The benchmark now contains 32 cases: 23 positive and 9 clean.

## Consequences

Overload additions are no longer false positives, and overload removals or
changes are visible. Template parameter changes are structural, not semantic
instantiation checks. Public API values for templates and overloads are more
verbose; `public-api` evidence shows the overload set.

## Alternatives considered

- Give every overload a distinct API key. Rejected: adding the second overload
  would remove the original unqualified key and create a false break.
- Keep overwriting overloads. Rejected: misses nontrivial removals and changes.

## Rollback

Remove the seven cases, restore last-signature-wins API values, and remove
template aggregate extraction.
