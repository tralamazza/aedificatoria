# 0040. Usefulness evaluation harness

- Status: accepted
- Date: 2026-11-27

## Context

The test suite verifies implementation behavior but cannot show whether the
guard catches consequential changes at acceptable review cost. Prior review
recommended a labeled mutation corpus and agent A/B trial.

## Decision

Add `benchmarks/usefulness/` with:

- a deterministic, versioned mutation corpus (`cases.json`);
- a runner that creates temporary repositories, applies changes, runs the public
  `aed check --json` command, and reports case-level precision, recall,
  false-block rate, and review findings on clean cases;
- a CI gate requiring recall >= 0.9 and clean-case false-block rate <= 0.05;
- a JSONL aggregation protocol for agent baseline-vs-guarded trials.

The corpus includes positive and clean cases for all core rule families and
regressions for P1/P2/P3 false positives and false negatives.

## Consequences

The benchmark catches harness and rule regressions on every CI run. Its perfect
score is not evidence of real-world precision: the corpus is deliberately
labeled and small. Agent A/B remains the authoritative usefulness test, with a
held-out task set and human grading. Real-world history audits remain diagnostic
rather than labeled evaluation.

## Alternatives considered

- Treat existing tests as usefulness evidence. Rejected: tests encode the same
  assumptions as the implementation.
- Run real-world parsing in CI. Rejected: network, clone state, and runtime make
  it non-hermetic.
- Automate model calls in this repository. Rejected: model/provider credentials,
  cost, and nondeterminism belong in a separate evaluation harness.

## Rollback

Remove `benchmarks/usefulness/`, its CI step, ADR 0040, and the README link.
