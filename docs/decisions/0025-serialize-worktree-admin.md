# 0025. Serialize Git worktree metadata operations

- Status: accepted
- Date: 2026-11-27

## Context

ADR 0024 gave temporary worktrees unique basenames. A 1,000-commit Click
history at `--jobs 12` still failed:

```text
fatal: failed to read .git/worktrees/tree-aed-base-.../commondir
```

The remaining race is Git worktree administration itself: concurrent
`git worktree add`/`remove` processes mutate and read shared
`.git/worktrees` metadata. Unique paths do not serialize that metadata.

## Decision

Serialize only worktree administration per repository:

- Acquire an exclusive `flock` on
  `<tmp>/aed-worktree-<sha1(repo)>.lock` around `git worktree add` and
  `git worktree remove`.
- Metric collection and file analysis remain parallel across processes.
- Each worktree still has a unique basename (`tree-<temp-root>`) and is removed
  in a `finally` block.
- On platforms without `fcntl`, retain the previous behavior; the current
  supported CI/development platforms are POSIX.

## Alternatives considered

- Serialize all history metric work. Rejected: destroys the `--jobs` speedup.
- Use `git archive` into temp directories. Rejected for now: changes checkout
  semantics (submodules, modes, symlinks) across every check and needs broader
  testing.
- Rely on unique worktree names. Rejected: 1,000 commits at 12 workers still
  reproduced the metadata race.
- Add a per-worktree lock. Rejected: the race is on shared `.git/worktrees`
  metadata, not on the individual worktree path.

## Consequences

Worktree creation/removal is serialized, adding a small per-commit admin cost;
parsing, metrics, and checks stay parallel. A stale lock file is harmless because
`flock` is released by the kernel when the holder exits. Lock files remain in the
temp directory and contain no repository data.

## Invariants

- Only one `git worktree add/remove` runs per repository at a time.
- Worktree creation and removal still happen for every commit.
- Analysis remains parallel and ordered by `pool.map`.
- The lock key is repository-specific.
- Failures still clean up the temp root and release the lock.

## Rollback

Remove `_worktree_lock` and the two lock blocks around worktree add/remove.
