# 0011. Opt-in GitHub API recovery for history audits

- Status: accepted
- Date: 2026-11-27

## Context

`aed history --head` recovers squashed PRs when the host still exposes pull
refs. If those refs are purged too, Git has no pointer to the original commits.
GitHub often retains them as PR data: the REST API can list the commits on a
closed PR and serve their trees even after the branch is deleted. Users asked
for a fallback for that case.

## Decision

Add an explicit, report-only `aed history --github-pr <N>` mode:

- Resolve the repository from `--repo owner/name` or the `origin` remote.
- List PR commits with `GET /repos/{owner}/{repo}/pulls/{N}/commits`, paginated
  and capped by GitHub at 250.
- Use a local commit/worktree when the SHA exists locally; otherwise download a
  tarball with `GET /repos/{owner}/{repo}/tarball/{sha}` and extract it safely.
- Read churn with `GET /repos/{owner}/{repo}/commits/{sha}`.
- Reuse the same metric collection and consecutive-commit comparison as local
  history, with zero growth caps.
- Read the token from `GITHUB_TOKEN` or `GH_TOKEN`; use `GITHUB_API_URL` for
  GitHub Enterprise. No token means unauthenticated requests only.
- Label the output as GitHub API recovery and include `source` in JSON.

This mode is never invoked by `aed check`, never affects the verdict, and never
runs without the explicit flag. The default guard remains offline and
deterministic.

## Alternatives considered

- Add networking to the normal history path. Rejected: it would make the
  default report depend on credentials, rate limits, and retention.
- Require users to script the API themselves. Rejected: the analysis would have
  to be reimplemented; aed already knows how to compute metrics from a tree.
- Recover only via PR refs. Rejected: refs can be purged; the API is the last
  host-backed source.
- Use tree/blob endpoints instead of tarballs. Rejected for now: far more
  requests; tarballs are one request per commit. The client can switch later.

## Consequences

The fallback requires network access, a token for private repositories, and
GitHub retaining the PR objects; 404 means unrecoverable. The 250-commit API cap
limits very large PRs. Tarball downloads are slower than local worktrees. Output
is explicitly non-hermetic and cannot be used as a CI gate. The local-first
behavior avoids downloads when the PR ref was already fetched.

## Invariants

- No network access without `--github-pr`.
- `aed check` never imports or calls the GitHub client.
- Local commit objects are preferred over downloads.
- Tarball extraction rejects absolute paths, `..`, links, and special files.
- API and authentication failures exit nonzero with a clear message.
- Output remains a report; exit 0 on success.

## Rollback

Remove the `--github-pr`/`--repo` flags, `github.py`, `evaluate_github`, the
tests, and this ADR. The offline `--head` path remains.
