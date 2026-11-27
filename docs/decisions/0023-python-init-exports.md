# 0023. Resolve Python package exports through `__init__.py`

- Status: accepted
- Date: 2026-11-27

## Context

Python packages commonly re-export symbols from private modules:

```python
# pkg/__init__.py
from ._internal import handle
```

The API check treated private modules as non-API and did not inspect imported
names, so a signature change in `_internal.handle` was invisible. `__all__`
entries were recorded as `"exported"` without resolving their signatures.

## Decision

Resolve package surfaces through local `__init__.py` re-exports:

- For every Python `__init__.py`, resolve relative and source-root-relative
  `from ... import ...` targets that are local files.
- Enumerate `__all__`; when present, imports not listed remain non-exported.
  Without `__all__`, explicit public imports are re-exported.
- Map aliases (`from ._internal import handle as run`) and nested class members
  (`Foo.method`) to the exported name.
- Keep unresolved exported names as `"exported"` so removal/renaming is still
  visible.
- Use the resolved surface in the per-file public-API check, baseline API
  surface, and history metrics.
- When a private Python module changes, compare the resolved `__init__.py`
  surfaces of its package ancestors. This catches private re-exports without
  treating every private symbol as public.
- Recurse through local package `__init__.py` targets, with a visited set.

Package-manager imports, dynamic `__getattr__`, and modules outside the
repository are not resolved; unresolved exports fall back to opaque exported
names.

## Alternatives considered

- Include every symbol from every private module. Rejected: makes private
  implementation details public and creates noise.
- Only resolve `__all__` names. Rejected: packages without `__all__` deliberately
  re-export imported names; those would remain invisible.
- Depend on the import graph. Rejected: the package graph is top-level and would
  miss private same-package re-exports.
- Compare private module signatures directly. Rejected: private implementation
  changes are not API unless re-exported.

## Consequences

Package `__init__.py` signatures become the API surface for re-exported symbols.
Public API metrics and baseline entries for package initializers are more
specific. Private module changes can produce a finding attributed to the
package initializer, which is the public boundary. Unresolved or dynamic exports
remain opaque. This is local static analysis; no imports or runtime execution.

## Invariants

- Private files are not API unless reached through an `__init__.py` re-export.
- `__all__` restricts inherited import exports when present.
- Aliases and nested class members map to the exported name.
- Unresolved exported names remain `"exported"`.
- A private-module change only reports if the resolved package surface changes.
- No network, import execution, or non-local module resolution.

## Rollback

Remove `_resolved_public_api`, `_package_initializers`, the resolved-surface
branches in checks/baseline, package-initializer checks, and this ADR.
