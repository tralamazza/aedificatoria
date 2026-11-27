# 0031. C/C++ support via tree-sitter

- Status: accepted
- Date: 2026-11-27

## Context

The guard supports Python, Rust, JavaScript, and TypeScript. C and C++ need the
same mechanical treatment for headers, complexity, tests, and dependency
boundaries. Running a compiler/CMake would make the guard non-hermetic and slow.

## Decision

Add one `c_cpp` adapter using pinned tree-sitter grammars:

- `tree-sitter-c==0.24.2` and `tree-sitter-cpp==0.23.4`; core dependencies, so
  C/C++ files are never silently skipped.
- `.c` uses the C grammar; `.cc/.cpp/.cxx/.hpp/.hh/.hxx/.ixx` use C++; `.h`
  prefers C++ and falls back to C when the C parse has fewer errors.
- Only headers are public API. Track functions, structs/unions/enums, typedefs,
  namespaces, classes, and public fields/methods. `static` declarations and
  non-public members are excluded.
- Complexity counts branch nodes in function bodies; macros/templates are not
  expanded.
- Tests recognize GoogleTest (`TEST`, `TEST_F`, `TEST_P`, `TYPED_TEST*`) and
  Catch2 (`TEST_CASE`, `SCENARIO`), assertions (`EXPECT_*`, `ASSERT_*`,
  `REQUIRE*`, `CHECK*`, `FAIL`), and skip tags.
- Includes: quoted includes resolve locally for component edges; angled includes
  become external dependencies unless they are C/C++ standard headers or local
  components. C/C++ standard headers are allowlisted.
- Package/component graph is directory-based: first path segment under
  `source_roots`, stripping a leading `include/`; CMake targets are deferred.
- `declared_dependencies` is empty; CMake/Conan/vcpkg dependency declarations
  are not parsed. `new-dependencies` therefore uses include roots only.

## Alternatives considered

- Use clang/CMake/compile_commands.json. Rejected: requires a toolchain and
  project build; non-hermetic and environment-dependent.
- Separate C and C++ adapters. Rejected: `.h` is ambiguous and includes/package
  logic is shared; one adapter avoids extension-order bugs.
- Treat every source file as API. Rejected: source symbols are implementation;
  headers are the public boundary.
- Parse CMake targets/dependencies now. Deferred: CMake is a language and build
  systems vary; directory components plus explicit limitations are safer.
- Fully expand macros/templates/includes with a preprocessor. Rejected for the
  deterministic offline model; document the limits.

## Consequences

C/C++ diffs get deterministic header API, complexity, test tampering, includes,
and component edges without a compiler. Known limits:

- macros are not expanded and are not API;
- C++ overloads share a key and parameter names remain in signatures;
- templates/ABI/layout are not resolved;
- CMake/Conan/vcpkg packages and include search paths are not parsed;
- directory components are coarser than CMake targets;
- `.h` grammar selection is heuristic.

## Invariants

- Grammar versions stay exactly pinned.
- Headers only enter public API.
- `static` and non-public class members are excluded.
- GoogleTest/Catch2 deleted tests and assertion drops ratchet.
- Standard C/C++ headers never become new dependencies.
- Local quoted includes can create package edges.
- No compiler, CMake, or network at analysis time.

## Rollback

Remove `cfamily.py`, the registry entry, grammar pins, C/C++ config paths, tests,
and this ADR.
