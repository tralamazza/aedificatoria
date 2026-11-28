# 0046. C/C++ mutation coverage in the usefulness benchmark

- Status: accepted
- Date: 2026-11-27

## Context

The mutation benchmark covered Python, Rust, JS/TS, configuration, and
architecture rules but had no C/C++ cases. The C/C++ audit had exposed
preprocessor and test-inventory gaps, so those paths needed labeled regression
coverage.

## Decision

Add eight C/C++ mutation cases:

- include-guarded/export-macro public API removal;
- export-macro signature change;
- libuv `TEST_IMPL` assertion removal;
- Unity `RUN_TEST` assertion removal;
- deleted singular `test/` C file;
- forbidden quoted-include edge between API and DB components;
- comment-only header change (clean);
- added C test assertion (clean).

The benchmark now contains 25 cases: 18 positive and 7 clean.

## Consequences

C/C++ API and test-tampering paths are covered by the mutation gate. The cases
exercise the public CLI and the same temporary-repository harness as other
languages. They remain labeled mutations, not real-world precision evidence.

## Alternatives considered

- Rely on the C/C++ audit only. Rejected: history findings are diagnostic and
  not labeled.
- Vendor large C/C++ projects into the test corpus. Rejected: size/licensing.

## Rollback

Remove the eight cases and the associated benchmark-count documentation.
