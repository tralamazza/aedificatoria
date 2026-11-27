# 0020. Check the staged index for pre-commit

- Status: accepted
- Date: 2026-11-27

## Context

The pre-commit hook ran `aed check` against the working tree, but `git commit`
commits the index. A developer could stage a weakening (deleted test, config
change) and then restore the file in the working tree; the hook passed while the
bad version entered the commit. Untracked files were also checked even though
they cannot be committed without being staged.

## Decision

Add `aed check --staged`:

- Diff the index against the base with `git diff --cached`.
- Materialize the index into a temporary tree with
  `git checkout-index --all --force --prefix=...`.
- Run every head-side check against that tree; the base remains a detached
  worktree of the base commit.
- Load `aed.toml` and the baseline from the staged tree, so a staged config or
  baseline weakening is evaluated as it will be committed.
- Exclude untracked files: they are not part of the commit.
- Use `--staged` in `.githooks/pre-commit`.

The default `aed check` remains working-tree based for CI and ordinary use. CI
checks the checkout after the merge/push, where index and working tree agree.

## Alternatives considered

- Check both the index and the working tree. Rejected: blocks commits for
  unrelated unstaged work and leaves the staged version ambiguous.
- Apply `git diff --cached` as a patch to a copy. Rejected: checkout-index is
  simpler and handles modes, deletions, and renames.
- Require users to stage everything and run the normal check. Rejected: normal
  check still reads the working tree, not the index.
- Make staged mode the default. Rejected: CI has no meaningful difference, and
  users inspecting an uncommitted working tree expect `aed check` to see it.

## Consequences

The hook now checks exactly the next commit. Staged-only checks create a full
temporary copy of the index; this is the same order of work as the existing
base worktree. Untracked files are intentionally ignored. Partial staging is
evaluated from the index, not from the working copy. `aed check` remains
read-only and leaves no index or worktree behind.

## Invariants

- `--staged` diffs only the index against the base.
- Untracked files never appear in staged check output.
- All head-side file reads, config loads, baselines, and language scans use the
  materialized index tree.
- The temporary index tree is removed on success and failure.
- `aed check` does not write the index or working tree.
- CI continues to use the default working-tree mode.

## Rollback

Remove `--staged`, `index_tree`, the staged branch in `changed_files`, and the
hook flag; restore the working-tree-only hook.
