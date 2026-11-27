# 0030. MIT license and precise pytest metadata detection

- Status: accepted
- Date: 2026-11-27

## Context

The repository was public but had no license or package license metadata. Adding
`license = { text = "MIT" }` to `pyproject.toml` also exposed a guard
false-positive: any `pyproject.toml` edit warned `test-config` whenever a
`[tool.pytest]` block existed, even if that block was unchanged.

## Decision

- License the project under MIT, copyright 2026 Daniel Tralamazza, in `LICENSE`.
- Publish the license in PyPI metadata via `pyproject.toml`.
- Document the license in `README.md`.
- For `pyproject.toml`, compare the parsed `tool.pytest` values between base and
  head. Warn only when that section actually changes. Preserve a warning fallback
  for unparsable TOML when a pytest section is present.
- Refresh the metric baseline after the guard change.

## Alternatives considered

- Use SPDX `license = "MIT"` only. Rejected for now: the table form works with
  the current hatchling/Python metadata path; SPDX expression support is a
  packaging upgrade.
- Keep warning on every `pyproject.toml` edit. Rejected: license, dependency, and
  project metadata changes are not verifier tampering.
- Leave the repository unlicensed. Rejected: public use and contributions need
  explicit terms.

## Consequences

The project is MIT-licensed and package metadata reports `License: MIT`.
Unrelated `pyproject.toml` edits no longer trigger `test-config`; actual pytest
configuration changes still do. The baseline is refreshed for the guard's added
complexity.

## Invariants

- `LICENSE` contains the standard MIT text and copyright holder.
- `pyproject.toml` declares MIT.
- README links to `LICENSE`.
- `test-config` warning tracks the parsed `tool.pytest` section, not the whole
  pyproject file.
- A pytest section added/removed/changed still warns.
- Baseline refresh is ADR-gated.

## Rollback

Remove the license metadata/README section, restore whole-file pyproject
warning behavior, and revert the baseline.
