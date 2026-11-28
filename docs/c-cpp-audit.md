# C/C++ real-world audit

Run: 2026-11-27. Pinned local clones, first-parent history, no repository writes.
Diagnostics only; findings are not labeled precision/recall.

Corpus: [`benchmarks/cfamily/repos.json`](../benchmarks/cfamily/repos.json)

## Post-fix results

The first run exposed that cJSON's public header was entirely wrapped in
preprocessor conditionals, so the adapter reported **0** public API symbols.
The adapter now recurses into `preproc_ifdef`, `preproc_if`, `preproc_else`, and
`preproc_elif` for API, complexity, and test inventory.

| Repository | Language | Window | Time | Findings | Commits with findings |
|---|---|---|---:|---:|---:|
| `fmtlib/fmt` | C++ | 100 commits | 18.5s | 64 | 26 |
| `nlohmann/json` | C++ | 100 commits | 32.8s | 58 | 16 |
| `libuv/libuv` | C | 100 commits | 13.0s | 75 | 31 |
| `google/googletest` | C++ | 100 commits | 21.5s | 153 | 24 |
| `DaveGamble/cJSON` | C | 100 commits | 4.2s | 74 | 26 |

All five runs exited successfully. Total wall time was ~90s with `--jobs 4`.

These values reflect the post-fix rerun after both preprocessor and C test-layout
fixes. Test files are now classified correctly, so less test code leaks into
complexity/API metrics and some finding counts dropped.

## 1,000-commit window

| Repository | Language | Commits available | Time | Findings | Commits with findings |
|---|---|---:|---:|---:|---:|
| fmt | C++ | 1,000 | 99.5s | 774 | 202 |
| json | C++ | 1,000 | 167.9s | 1,134 | 205 |
| libuv | C | 1,000 | 75.0s | 997 | 322 |
| googletest | C++ | 1,000 | 120.5s | 2,358 | 243 |
| cJSON | C | 387 | 12.8s | 293 | 87 |

All runs succeeded with `--jobs 8`; total wall time was ~476s. cJSON's clone has
fewer than 1,000 first-parent commits, so its full available history was used.
These are diagnostic history findings, not defect labels.

## Latest metrics

| Repository | Public API symbols | Complexity functions | Dependencies | Package edges | Tests | Assertions |
|---|---:|---:|---:|---:|---:|---:|
| fmt | 472 | 815 | 21 | 2 | 679 | 2,901 |
| json | 476 | 1,990 | 17 | 0 | 504 | 12,654 |
| libuv | 969 | 8,254 | 78 | 19 | 481 | 3,496 |
| googletest | 646 | 1,860 | 28 | 0 | 2,289 | 5,161 |
| cJSON | 105 | 940 | 0 | 2 | 225 | 447 |

## Signals

- **cJSON:** public API extraction is now non-zero (105 symbols at tip), up from
  zero. Include guards, export macros, `RUN_TEST`, and `TEST_ASSERT` inventory
  now work; 225 tests and 447 assertions are recognized.
- **libuv:** C API/complexity/edge extraction is strong. `TEST_IMPL` inventory now
  finds 481 tests and 3,496 assertions.
- **fmt/json:** header-heavy C++ libraries expose substantial API/complexity/test
  activity; template parameters, overload sets, and concepts are compared
  structurally.
- **C++20 modules:** exported declarations/blocks are extracted from `.ixx`,
  `.cppm`, and `.mpp`; partition names and `export import` edges are API
  identity, while non-exported declarations stay internal.
- **ABI:** public record field order, field width, virtual order, enum underlying
  type, packing/header attributes, and typedef records form a structural ABI
  fingerprint. Default initializers, deprecation attributes, and anonymous
  namespaces are excluded. This is target-independent and conservative, not a
  compiled layout oracle.
- **googletest:** its own test inventory is large; merge-only history dominates
  many findings.
- **Post-ADR-0047 smoke:** the 100-commit rerun reports fmt 108, json 124,
  libuv 75, googletest 195, and cJSON 74 findings. The increase over the
  pre-template/overload run is diagnostic volume from richer API signatures,
  not evidence of new real-world regressions.

## What this validates

- The adapter runs over real C and C++ repositories without crashes.
- It extracts API/complexity/test/dependency metrics at scale.
- Preprocessor-guarded headers and export macros no longer silently erase API.
- It exposes remaining coverage gaps rather than claiming C/C++ is complete.

## Limits

- First-parent history mixes intentional deprecations, large merges, and test
  rewrites; findings are diagnostics, not defects.
- 1,000-commit history is noisy; intentional deprecations, large migrations, and
  test rewrites appear as findings. cJSON has only 387 commits available.
- Pinned repositories are not vendored; the runner needs network on first use.
- Macros/ABI/CMake targets remain shallow or unresolved; template and concept
  checks are declarative, module extraction is export-surface only, partition
  contents are not merged, ABI is structural rather than compiled, and none are
  instantiation-semantic.

## Next depth work

1. Consider CMake target/include metadata as the next hermetic layer.
2. Consider module partitions/linkage and instantiation-level template checks.
3. Keep the mutation benchmark's C/C++ cases aligned with audit discoveries.
