# 0001. Record architecture decisions

- Status: accepted
- Date: 2026-11-27

## Context

Coding agents make locally reasonable, confident decisions whose architectural
cost appears later: coupling, cycles, leaky interfaces, and features that
require edits across many modules. The rationale for those decisions is usually
lost when the agent's context is compacted.

## Decision

Record every irreversible or high-blast-radius decision in a numbered ADR under
`docs/decisions/`. Each record states the context, the decision, the
alternatives, the consequences, the invariants, and the rollback path.
One-way-door changes without a record fail `aed check`.

## Alternatives considered

- Rely on the agent's chat history. Rejected: it is lossy and not reviewable.
- Rely on code comments alone. Rejected: they do not capture alternatives or rollback.

## Consequences

Decisions become durable, reviewable artifacts. The guard can compare the
current diff against them instead of relying on model confidence.

## Invariants

- A one-way-door change has an accepted or proposed ADR.
- Tests are not deleted, weakened, or skipped to make a change pass.
- New package cycles and forbidden dependency edges fail the check.

## Rollback

Delete the proposed ADR; for an accepted decision, write a superseding ADR.
