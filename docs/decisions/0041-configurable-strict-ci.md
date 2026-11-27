# 0041. Configurable strict CI

- Status: accepted
- Date: 2026-11-27

## Context

`aed check` exits `0` for warnings by default. Projects that want CI to fail on
warnings had to inspect JSON with `jq`, which is not configurable through the
guard itself. The policy must remain a verifier policy protected by `guard-config`.

## Decision

- Add `[aed] fail_on_warn = false` (default).
- Add `aed check --strict` as a one-shot override.
- Exit codes:
  - `0`: allow, or review without strict mode;
  - `1`: review with strict mode;
  - `2`: block (errors), and CLI/config/git failures.
- Treat `fail_on_warn` as guard configuration; changing it requires an accepted ADR.
- Expose it in `aed rules`.

## Consequences

CI can opt into warnings-as-failures without `jq` or a wrapper. Strict mode is
distinct from blocking errors, so agents can tell warnings from hard failures.
The default remains backward-compatible.

## Alternatives considered

- Always fail warnings. Rejected: warnings are often intentional review gates.
- Use only `jq` in CI. Rejected: configuration belongs in the guard policy.
- Make strict mode a separate command. Rejected: the check verdict is the same;
  only the exit policy differs.

## Rollback

Remove `fail_on_warn`, `--strict`, the exit-code branch, tests, and this ADR.
