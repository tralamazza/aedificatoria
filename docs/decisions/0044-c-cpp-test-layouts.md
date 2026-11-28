# 0044. Common C/C++ test layouts and plain-C test inventory

- Status: accepted
- Date: 2026-11-27

## Context

The C/C++ real-world audit showed zero tests for libuv and cJSON. Their layouts
are common C conventions:

- libuv: `test/test-*.c` with `TEST_IMPL(...)`, `ASSERT_*`;
- cJSON: `tests/*.c` with Unity-style `RUN_TEST(...)` and `TEST_ASSERT_*`;
- a root `test.c`/`test.cpp` entry point.

Default test paths recognized only `tests/**` and underscore-style names, and
C test inventory recognized only GoogleTest/Catch2.

## Decision

- Add singular `test/**` and `**/test/**` layouts.
- Add hyphenated C/C++ test names, root `test.c`/`test.cpp`, and matching
  template/config documentation.
- Recognize `TEST_IMPL(name)` as a test function.
- Recognize Unity `RUN_TEST(name)` references and map their assertions from the
  referenced function body.
- Count `TEST_ASSERT` and bare `ASSERT` macros as assertions.

## Consequences

libuv and cJSON test files are now classified as tests, and their test/assertion
metrics are populated. More files may be treated as tests in repositories using a
singular `test/` directory; that is intentional for test-tampering protection.

## Alternatives considered

- Hard-code libuv/cJSON names. Rejected: fragile and non-general.
- Count every function in a test directory as a test. Rejected: helpers and
  callbacks would distort per-test assertion counts.
- Execute CTest/Unity. Rejected: non-hermetic and outside static analysis.

## Rollback

Remove the added default paths and C test-inventory branches, and the regression
test/report update.
