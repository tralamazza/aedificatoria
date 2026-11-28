# C/C++ real-world audit

Run: 2026-11-27. Pinned local clones, first-parent history, no repository writes.
Diagnostics only; findings are not labeled precision/recall.

Corpus: [`benchmarks/cfamily/repos.json`](../benchmarks/cfamily/repos.json)

| Repository | Language | Window | Time | Findings | Commits with findings |
|---|---|---|---:|---:|---:|
| `fmtlib/fmt` | C++ | 100 commits | 38s | 36 | 22 |
| `nlohmann/json` | C++ | 100 commits | 33s | 39 | 16 |
| `libuv/libuv` | C | 100 commits | 23s | 69 | 32 |
| `google/googletest` | C++ | 100 commits | 26s | 12 | 11 |
| `DaveGamble/cJSON` | C | 100 commits | 4s | 57 | 24 |

All five runs exited successfully. Total wall time was ~124s with `--jobs 4`.

## Latest metrics

| Repository | Public API symbols | Complexity functions | Dependencies | Package edges | Tests | Assertions |
|---|---:|---:|---:|---:|---:|---:|
| fmt | 268 | 1,924 | 43 | 3 | 483 | 2,406 |
| json | 247 | 1,222 | 17 | 0 | 439 | 11,780 |
| libuv | 204 | 10,258 | 84 | 24 | 0 | 0 |
| googletest | 371 | 1,775 | 28 | 1 | 1,922 | 4,463 |
| cJSON | 0 | 932 | 0 | 3 | 0 | 0 |

## Finding categories

| Repository | Test inventory | Total complexity | Function complexity | API growth | API removed/changed | Dependencies | Edges | Large/merge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fmt | 16 | 10 | 4 | 2 | 1 | 1 | 0 | 2 |
| json | 1 | 6 | 7 | 4 | 10 | 0 | 0 | 11 |
| libuv | 0 | 31 | 34 | 0 | 0 | 1 | 2 | 1 |
| googletest | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 10 |
| cJSON | 0 | 24 | 33 | 0 | 0 | 0 | 0 | 0 |

## Signals

- **fmt:** meaningful API/complexity/test activity; header templates are parsed
  structurally but not instantiated.
- **json:** many large atomic changes and public API changes; consistent with a
  header-only library with dense version history.
- **libuv:** complexity and include-edge activity; its C tests are not recognized
  by the current test paths (`test/`, `test-*.c`), so test inventory is zero.
- **googletest:** its own GoogleTest inventory is large (1,922 tests); merge-only
  history dominates its small finding count.
- **cJSON:** public API symbols are **zero** despite a public header. This is a
  concrete macro/export gap: declarations wrapped in export macros are not
  recognized as function declarators. Its C test layout is also not recognized.

## What this validates

- The adapter runs over real C and C++ repositories without crashes.
- It extracts API/complexity/test/dependency metrics at scale.
- It catches plausible history changes in fmt/json/libuv/googletest.
- It exposes concrete coverage gaps rather than claiming C/C++ is complete.

## Limits

- First-parent history mixes intentional deprecations, large merges, and test
  rewrites; findings are diagnostics, not defects.
- Only 100 commits per repository; no 1,000-commit C/C++ window yet.
- Pinned repositories are not vendored; the runner needs network on first use.
- Macros/templates/ABI/CMake targets remain shallow or unresolved.

## Next depth work

1. Add export-macro handling for C headers (cJSON-style `CJSON_PUBLIC(...)`).
2. Add common C test layouts (`test/`, `test-*.c`) or explicit CTest inventory.
3. Add targeted mutation cases for C/C++ API macro declarations and include
   direction using these real headers.
4. Measure a 1,000-commit window before considering CMake/libclang depth.
