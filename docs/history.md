# History audit

`aed history` is a report, not a gate. It exits `0`, writes nothing, and never
runs as part of `aed check`. It reconstructs commits in temporary worktrees,
collects the baseline metrics, and compares each commit to its first parent with
zero growth caps so accepted regressions become visible.

## Modes

| Mode | Command | Behavior |
|---|---|---|
| First-parent | `aed history --limit 100` | Walk the mainline only. Merge commits are net diffs. |
| All reachable | `aed history --all --limit 400` | Walk every reachable commit and compare each to its first parent. Captures side-branch commits. |
| Window | `--since REF` / `--head REF` | Limit or redirect the local audit. `--head` accepts any ref or commit. |
| PR/MR recovery | `--github-pr N` / `--gitlab-mr IID` | Fetch commit history through `gh`/`glab` when local commits are gone. |

`--jobs N` parallelizes local metric collection. Each worker gets its own parser
and temporary worktree; order and JSON match `--jobs 1`. Keep `N <= CPU cores`.
Forge API modes do not use `--jobs` and cannot be combined with `--since` or
`--head`.

`--json` emits `{kind, source?, commits, worst}`. Each commit contains `commit`,
`date`, `subject`, `parents`, `kind`, `pr`, `churn`, `metrics`, `regressions`,
and `impact`. `worst` is the ranked subset of commits with regressions. In
first-parent mode, the first commit in a window has no previous commit and
therefore no regressions; this is expected, not evidence of a clean commit. In
`--all` mode, a parent outside the window is loaded on demand, so the first listed
commit can still have regressions.

## Topology semantics

| Shape | What history sees |
|---|---|
| Ordinary commit | Diff against its parent. |
| Merge commit | Net diff against the first parent. `--all` adds side-branch commits. |
| Squash merge | One single-parent commit. Internals are gone from the repository. |
| Rebase merge | Linear single-parent commits; every commit is analyzed. |
| Octopus merge | N-parent merge; first parent is the mainline net; `--all` covers every side. |
| Conflict resolution | `merge-only lines` heuristic from `git show --cc`. |

### Squashes

A single-parent commit whose subject ends in `(#N)` is labeled `squash`. This is
a heuristic; it can mislabel a rebased commit whose subject already ends in a PR
number. Large squashes warn that internal commits are unavailable.

For architecture-sensitive work, inspect the PR branch before squashing:

```sh
aed history --since origin/main
aed check --base origin/main
```

Squashing permanently discards the intermediate commits from the mainline.
Prefer a merge or rebase merge if the commit sequence matters.

### Merges

A merge is not atomic: its branch commits remain reachable. A large merge net
diff produces `large merge` and points at `--all`. First-parent history sees only
the net diff; use `--all` or the PR/MR mode for internals.

Conflict-resolution edits exist on neither parent. `git show --cc` reports files
with lines added/removed relative to every parent as:

```text
merge-only lines in N file(s) (potential conflict resolution): ...
```

Same-file auto-merges that only combine parent lines are not flagged. Pure
reorders, exact one-parent resolutions, and some binary conflicts are missed.
This is a heuristic, not proof that a conflict existed.

### Octopus merges

Octopus merges use the generic N-parent path:

- `kind` is `merge` and `parents` reports the real count;
- the merge remains a net diff against its first parent;
- `--all` walks every parent branch;
- combined-diff prefix width equals the parent count, so merge-only detection
  works for any N;
- clean octopus merges produce no merge-only finding.

Git's octopus strategy refuses content conflicts, so ordinary octopus merges are
clean. Manually constructed octopus commits can still contain conflict-resolution
edits and are inspected.

### Rebase merges

Rebase merges are ordinary linear single-parent commits. Default first-parent
history already sees every commit, so `--all` adds nothing for a clean rebase.
There is no merge-only signal. A conflict resolved during rebase is baked into a
rewritten commit and appears as a normal commit change; Git does not record that
it was a rebase conflict. Original SHAs are rewritten, so use PR/MR history for
the pre-rebase sequence.

## Reading the report

- `impact` is weighted: cycles/API/dependencies rank above test inventory. Sort by
  impact, then finding count.
- History forces baseline growth caps to `0`. Intentional deprecations and small
  refactors therefore look like regressions; judge magnitude and context.
- Merge test findings are often topology noise. Compare net tests/assertions; do
  not treat raw counts as defect counts.
- Public API ignores private paths (`_module.py`, `_dir/`, except `__init__.py`).
  `metric_excludes` keeps docs/examples/benchmarks out of production metrics.
- Per-function complexity findings start with a backtick; total complexity starts
  with `total complexity`; API removals with `public symbol`.

## Recovering a squashed PR

Hosting providers often retain pull-request refs after branch deletion. Fetch
them locally; aed never touches the network:

```sh
# GitHub
git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'
aed history --head refs/remotes/origin/pr/123 --since origin/main

# GitLab
git fetch origin '+refs/merge-requests/*/head:refs/remotes/origin/merge-requests/*'
aed history --head refs/remotes/origin/merge-requests/123/head --since origin/main
```

`--head` also works with a preserved branch, merge-queue ref, or commit recovered
from `git fsck`.

### GitHub API fallback

```sh
gh auth login          # or export GH_TOKEN
aed history --github-pr 123 --repo owner/repo --limit 20
```

Uses `gh api` to list PR commits (`GET /pulls/{n}/commits`, capped by GitHub at
250), downloads a source tarball per commit, and reads commit stats. Local commit
objects are used when available. `GH_HOST` selects GitHub Enterprise. This is
non-hermetic: it requires `gh`, network, credentials, and GitHub retention. A
purged commit returns 404. Persist `aed history --json` at merge time for a
durable audit.

### GitLab API fallback

```sh
glab auth login        # or export GITLAB_TOKEN
aed history --gitlab-mr 123 --repo group/subgroup/repo --limit 20
```

Uses `glab api` to list MR commits, downloads a source archive per commit, and
reads commit stats plus the diff list. Project paths may contain subgroups and
are URL-encoded. `glab` handles auth, pagination, `GITLAB_TOKEN`/`OAUTH_TOKEN`,
and `GITLAB_HOST`. Same non-hermetic and retention limits as GitHub.

## Limits

- Local history uses the current configuration for every commit.
- Historical ADRs, one-way doors, and config weakening are not re-evaluated.
- Cost grows with `--limit` (and substantially with `--all`).
- Squash detection from `(#N)` is a message heuristic.
- `--all` does not recover internals from a squash and does not change a merge
  commit's first-parent net diff.
- Conflict-resolution detection is line-level and heuristic.
- API fallback modes are opt-in and never part of `aed check`.
