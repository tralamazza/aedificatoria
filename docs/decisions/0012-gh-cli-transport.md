# 0012. Use the GitHub CLI as the opt-in API transport

- Status: accepted
- Date: 2026-11-27
- Supersedes: 0011 (urllib transport)

## Context

ADR 0011 added `aed history --github-pr` with a hand-written `urllib` client:
manual token lookup, headers, pagination, redirect handling, and a separate
`GITHUB_API_URL` setting. The GitHub CLI (`gh api`) already handles auth
(keyring, `GH_TOKEN`), pagination (`--paginate --slurp`), redirects, enterprise
hosts (`GH_HOST`), rate-limit errors, and binary downloads (tarballs). The mode
is explicitly opt-in and report-only, so adding an external tool here does not
threaten the deterministic `aed check` path.

## Decision

Use `gh api` as the transport for `--github-pr`:

- `pr_commits` calls `gh api --paginate --slurp repos/{owner}/{repo}/pulls/{N}/commits`
  and flattens the page arrays.
- Commit stats come from `gh api repos/{owner}/{repo}/commits/{sha}`.
- Tarballs come from `gh api repos/{owner}/{repo}/tarball/{sha}`; extraction stays
  local and safe.
- Authentication is whatever `gh` uses: interactive login, `GH_TOKEN`, or
  `GITHUB_TOKEN` as supported by `gh`. `GH_HOST` selects Enterprise.
- No direct HTTP code, token parsing, or `GITHUB_API_URL` remains.

`aed check` still never imports or calls the GitHub client. `--github-pr` remains
report-only, commit-order within the PR commit list, and explicit.

## Alternatives considered

- Keep the `urllib` client. Rejected: it duplicates auth, pagination, redirects,
  and error formatting that `gh` already provides, and was more code to test.
- Support both transports dynamically. Rejected: more branches and an ambiguous
  auth story for marginal benefit.
- Use `gh pr view --json commits` only. Rejected: it does not provide tarballs or
  per-commit stats; `gh api` is the right primitive.

## Consequences

`--github-pr` now requires `gh` installed and authenticated, including for public
repositories in automation; missing or unauthenticated `gh` produces a clear
error. The mode remains non-hermetic and retention-dependent. Tests use a fake
client plus subprocess-level tests for pagination and missing binaries; no test
requires network. The `aed check` boundary is unchanged.

## Invariants

- `gh` is only invoked when `--github-pr` is passed.
- `aed check` never invokes `gh` or the network.
- Tarball extraction rejects absolute paths, `..`, links, and special files.
- Pagination is flattened before commit selection; the 250-commit API cap is
  documented.
- Failures exit nonzero with `gh`'s error detail.

## Rollback

Restore the `urllib` client from ADR 0011's implementation and this ADR's commit
history; the analysis and metric code stay unchanged.
