# 0013. GitLab merge-request recovery through glab

- Status: accepted
- Date: 2026-11-27

## Context

ADR 0012 established `aed history --github-pr` on top of `gh`. GitLab has the
same problem and an analogous CLI: MR refs can be purged after a squash, while
the GitLab API may still retain the MR commits. `glab api` handles GitLab auth,
pagination, project placeholders, enterprise hosts, and binary archive downloads.

## Decision

Add an explicit, report-only `aed history --gitlab-mr <IID> --repo group/repo`
mode:

- Move shared transport primitives (`ForgeError`, `ForgeCommit`,
  `extract_tarball`) to `forge.py`; both GitHub and GitLab clients use them.
- Rename `history.evaluate_github` to `history.evaluate_forge`; it accepts any
  client with `pr_commits(number, limit)` and a `tarball(sha)` context manager.
- `GitLabClient` shells out to `glab api`:
  - MR commits: `projects/{urlencoded-path}/merge_requests/{iid}/commits`;
  - commit stats: `.../repository/commits/{sha}`;
  - file count: `.../repository/commits/{sha}/diff?per_page=100`, paginated;
  - archive: `.../repository/archive.tar.gz?sha={sha}`.
- Project paths include subgroups, so the entire `group/subgroup/repo` path is
  URL-encoded; `--repo` overrides the origin remote.
- Auth is whatever `glab` uses: `glab auth login`, `GITLAB_TOKEN`/`OAUTH_TOKEN`,
  and `GITLAB_HOST`. `glab` runs with the repository as cwd so its host
  resolution matches the checkout.
- No network without the flag; `aed check` never imports or invokes either forge
  client.

## Alternatives considered

- Implement GitLab over raw HTTP. Rejected: duplicates auth, pagination, and
  enterprise-host handling that `glab` already provides.
- Use GitLab MR refs only. Rejected: refs can be purged; the API is the fallback.
- Reuse the GitHub client with a different base URL. Rejected: the API shapes,
  project addressing, archive endpoint, and commit fields differ.

## Consequences

The GitLab fallback requires `glab` installed and authenticated; missing or
unauthorized `glab` fails clearly. It makes one extra API request per commit for
the diff list, so large MRs are slower than local history. Retention is not
guaranteed. The shared forge types keep the GitHub path unchanged except for
the renamed types and the generic `evaluate_forge`.

## Invariants

- `gh`/`glab` are only invoked with the explicit forge flag.
- `aed check` never invokes a forge CLI or the network.
- Project paths are fully URL-encoded; subgroups are preserved.
- Archive extraction rejects absolute paths, `..`, links, and special files.
- Local commit objects are preferred over downloads.
- Failures exit nonzero with the CLI's error detail.

## Rollback

Remove `--gitlab-mr`, `gitlab.py`, the shared `forge.py` (restoring the GitHub
types), the tests, and this ADR.
