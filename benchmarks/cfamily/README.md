# C/C++ real-world audit corpus

This is a pinned, diagnostic corpus for the C/C++ adapter. It does not vendor
third-party source or add a network dependency to CI.

Repositories:

| ID | Language | Focus |
|---|---|---|
| `fmt` | C++ | headers, templates, API, tests, CMake |
| `json` | C++ | header-only API, tests, includes |
| `libuv` | C | C headers, API, tests, CMake |
| `googletest` | C++ | C++ API, GoogleTest inventory, CMake |
| `cjson` | C | C API, tests, CMake |

`repos.json` pins each repository to a commit. Licenses remain with the upstream
projects.

Run with network access:

```sh
python benchmarks/cfamily/run.py \
  --cache /tmp/aed-cfamily-corpus \
  --limit 100 \
  --jobs 4 \
  --output /tmp/cfamily-audit.json
```

Re-run from an existing cache without network:

```sh
python benchmarks/cfamily/run.py --offline --output /tmp/cfamily-audit.json
```

The 100-commit run is the quick smoke. The diagnostic deep pass uses 1,000
commits:

```sh
python benchmarks/cfamily/run.py \
  --offline --limit 1000 --jobs 8 \
  --output /tmp/cfamily-audit-1k.json
```

## What it measures

For each repository, the runner executes the public `aed history --json` path over
the last `--limit` first-parent commits and records:

- commits analyzed;
- findings and commits with findings;
- finding counts by rule;
- `worst` history points;
- wall time and failures.

This audits the adapter through the real CLI: parsing, public API extraction,
complexity, test inventory, dependency/include edges, and incremental history.

## Interpretation

The corpus is **not** labeled precision/recall and is not a gate. History findings
include intentional API changes, large commits, and test rewrites. Use it to find
crashes, pathological runtime, empty C/C++ metrics, and systematic false-positive
patterns, then build targeted labeled cases from those observations.

Results: [`docs/c-cpp-audit.md`](../../docs/c-cpp-audit.md).
