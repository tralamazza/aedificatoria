# 0006. The verifier configuration is a one-way door; assertions ratchet per test

- Status: accepted
- Date: 2026-11-27

## Context

`aed check` loads `aed.toml` from the working tree. One small committed line can
disable a check (`block_removed_tests = false`), widen an allowlist, add an
exclude, remove a one-way door, or raise a threshold. The guard then evaluates
the same diff with the weakened configuration and passes. Separately, assertion
tampering was counted per file: an agent could remove an assertion from one test
and add a trivial one elsewhere, keeping the total flat.

Both are silent bypasses of the verifier, not ordinary code drift.

## Decision

1. Add the `guard-config` rule. Compare the effective configuration loaded from
   the base worktree with the head working tree, using the same path (`aed.toml`
   by default, or `--config`). Compare every policy field except
   `cargo_advisory`. Any change requires a valid ADR in the same diff: without
   one it is an error; with one it is a warning. The rule runs independently of
   the head configuration, so disabling checks cannot disable this comparison.
2. Record per-test assertion counts in `TestInfo.counts` for Python, Rust, and
   JS/TS. During test tampering, any existing test whose assertion count drops
   is an error, even if another test gained assertions. The file-level total
   remains as a fallback when per-test data is absent.

## Alternatives considered

- Keep the configuration trusted. Rejected: it is the verifier's control plane;
  a one-line change silently changes every verdict.
- Keep only file-level assertion counts. Rejected: it lets assertions be moved
  between tests to hide a weakened test.
- Make every config weakening an unconditional error. Rejected: legitimate
  policy changes need a documented path; an ADR is the project's existing
  mechanism. ADR-authorized weakening is a visible review decision.
- Analyze assertion strength semantically. Rejected: non-deterministic and
  outside the guard's thesis.

## Consequences

Configuration changes are now explicit, reviewable decisions. Assertion
laundering between tests is blocked. Residual gaps remain: an ADR-authorized
weakening still exits 0, replacing a strong assertion with a weak one while
keeping the count is semantic and not detected, and cumulative drift across
many small commits is still outside the per-diff model.

## Invariants

- `guard-config` never uses the head configuration to decide whether to run.
- Any effective config change, except `cargo_advisory`, needs a valid ADR.
- Per-test assertion counts never increase tolerance for an existing test.
- Python, Rust, and JS/TS adapters all populate `TestInfo.counts`.

## Rollback

Remove `check_guard_config` and the `TestInfo.counts` field, and restore
file-level assertion checks.
