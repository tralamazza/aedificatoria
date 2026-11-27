# 0022. Ratchet JS/TS `.each` parameterized test cases

- Status: accepted
- Date: 2026-11-27

## Context

Jest/Vitest-style `test.each` / `it.each` tables define multiple test cases with
one test registration. Removing a row from the table weakens coverage without
removing a test function, so the existing test-tampering and baseline checks did
not notice it. The test collector also skipped the nested `.each(...)` and
template forms entirely.

## Decision

Track parameterized case counts in `TestInfo.cases`:

- Recognize `test.each([...])("name", ...)`, `it.each(...)`, and tagged-template
  `test.each\`...\`` forms, including `describe.each` nesting.
- Count literal array rows and template rows containing `${...}`. A variable
  table is still recognized as a test but contributes no case count.
- On a changed file, if a test still exists and its case count decreases, emit
  `test-tampering` error.
- Store counts in baseline schema v3 as `test_cases[file][test]`. If the test
  still exists and its count decreases against the baseline, emit a `baseline`
  error. Schema v1/v2 files remain readable and skip the case ratchet.
- Keep assertion counts as before; cases are a separate ratchet.

## Alternatives considered

- Name each generated case as a separate test. Rejected: table rows have no
  stable names, generated naming would create noisy/removal-prone keys, and the
  shared callback assertion count would be misleading.
- Execute Jest/Vitest to enumerate cases. Rejected: requires Node/project
  execution and violates the offline deterministic model.
- Diff the table source text. Rejected: formatting changes would look like
  tampering; structural row counts are stable.
- Only check per-diff and not baseline. Rejected: a table shrink could be
  accepted in one commit and never noticed afterwards.

## Consequences

Literal and template `.each` tables now ratchet in both the diff and persistent
baseline. The collector recognizes variable tables but cannot count their rows;
removing a test registration is still caught. Baseline schema v3 is larger by
one case-count map per test file. Python/Rust behavior is unchanged.

## Invariants

- A `.each` test still present with fewer literal/template cases is an error.
- A normal test is not assigned a case count.
- Variable `.each` tables do not invent a case count.
- Baseline v1/v2 remain readable; case ratcheting starts from v3 data.
- Case counts are per test name, not global file totals.
- No Node/network execution.

## Rollback

Remove `TestInfo.cases`, `.each` counting, test-tampering/baseline case checks,
the `test_cases` baseline field, and this ADR.
