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
| `fmtlib/fmt` | C++ | 100 commits | 38.3s | 70 | 31 |
| `nlohmann/json` | C++ | 100 commits | 32.8s | 58 | 16 |
| `libuv/libuv` | C | 100 commits | 22.4s | 94 | 39 |
| `google/googletest` | C++ | 100 commits | 26.4s | 154 | 25 |
| `DaveGamble/cJSON` | C | 100 commits | 4.2s | 72 | 25 |

All five runs exited successfully. Total wall time was ~124s with `--jobs 4`.

The commit-with-findings values above reflect the post-fix rerun; findings rose
because declarations and tests inside conditional preprocessor blocks were
previously invisible.

## Latest metrics

| Repository | Public API symbols | Complexity functions | Dependencies | Package edges | Tests | Assertions |
|---|---:|---:|---:|---:|---:|---:|
| fmt | 778 | 2,787 | 43 | 3 | 679 | 2,901 |
| json | 476 | 1,990 | 17 | 0 | 504 | 12,654 |
| libuv | 993 | 11,581 | 84 | 24 | 0 | 0 |
| googletest | 1,061 | 2,778 | 28 | 1 | 2,289 | 5,161 |
| cJSON | 105 | 956 | 0 | 3 | 0 | 0 |

## Signals

- **cJSON:** public API extraction is now non-zero (105 symbols at tip), up from
  zero. The include guard and export macro are handled by preprocessor recursion;
  its C tests remain unrecognized because of the `test/*.c` layout.
- **libuv:** C API/complexity/edge extraction is strong, but its tests remain
  unrecognized for the same C test-layout reason.
- **fmt/json:** header-heavy C++ libraries expose substantial API/complexity/test
  activity; templates are structural only.
- **googletest:** its own test inventory is large; merge-only history dominates
  many findings.

## What this validates

- The adapter runs over real C and C++ repositories without crashes.
- It extracts API/complexity/test/dependency metrics at scale.
- Preprocessor-guarded headers and export macros no longer silently erase API.
- It exposes remaining coverage gaps rather than claiming C/C++ is complete.

## Limits

- First-parent history mixes intentional deprecations, large merges, and test
  rewrites; findings are diagnostics, not defects.
- Only 100 commits per repository; no 1,000-commit C/C++ window yet.
- Pinned repositories are not vendored; the runner needs network on first use.
- Macros/templates/ABI/CMake targets remain shallow or unresolved.

## Next depth work

1. Add common C test layouts (`test/`, `test-*.c`) or explicit CTest inventory.
2. Add targeted mutation cases for C/C++ API macro declarations and include
   direction using these real headers.
3. Measure a 1,000-commit window before considering CMake/libclang depth.
