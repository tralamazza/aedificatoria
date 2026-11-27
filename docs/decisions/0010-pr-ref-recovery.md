# 0010. Audit surviving pull-request refs after a squash

- Status: accepted
- Date: 2026-11-27
- Amended by: 0011-0013 (opt-in forge API recovery)

> Later: the default `--head` path stays offline. Opt-in `--github-pr`/
> `--gitlab-mr` recovery was added by ADRs 0011-0013.

## Context

A squash merge leaves one single-parent commit on the protected branch. The
feature branch is usually deleted, so `aed history` walking `HEAD` can only see
the net tree. The user asked how to audit the internals after the fact. From Git
alone this is impossible: a squash commit contains no parent pointer to the
original commits, and commit messages do not carry trees.

Hosting providers often retain pull-request refs after branch deletion. GitHub
keeps `refs/pull/<N>/head` (for both open and closed PRs, including fork PRs) in
the base repository. Those refs point at the original PR head, which still
contains every branch commit.

## Decision

Add `--head <ref>` to `aed history`. It walks the first-parent chain ending at
that revision instead of `HEAD`, and works with any local ref or commit. The
default path stays offline: it never fetches or calls a hosting API. The
documented recovery workflow is:

```sh
git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'
aed history --head refs/remotes/origin/pr/123 --since origin/main
```

The same option inspects a preserved branch, a merge-queue ref, or a commit
recovered with `git fsck`. If the host has purged the PR refs and no artifact was
saved, the internals are unrecoverable; the command cannot invent them. Persist
`aed history --json` at merge time when a durable audit is required.

## Alternatives considered

- Call the GitHub API from the default `aed history` path. Rejected: network,
  credentials, and host coupling in a deterministic offline guard. Opt-in
  recovery was added later by ADRs 0011-0013.
- Reconstruct commits from the squash message. Rejected: messages contain no
  trees and are not authoritative.
- Require merge commits. Rejected: merge strategy is a repository policy; the
  guard reports the tradeoff instead of enforcing it.

## Consequences

Post-hoc audits are possible when the host retains PR refs, which is the common
case but not a guarantee. The workflow requires an explicit fetch, so it remains
deterministic and offline. A squash whose PR refs were purged still has only net
metrics; the pre-merge guard and stored reports are the only record. The
`--head` option also makes the history command usable for released tags, release
branches, and recovered commits.

## Invariants

- `--head` is read-only and works with any resolvable revision.
- No network access in the default path; fetching is the user's explicit action.
  Opt-in forge recovery is separate (ADRs 0011-0013).
- The first-parent chain of the chosen ref is what gets audited.
- If a revision cannot be resolved, the command fails with a Git error.

## Rollback

Remove `--head` from the history command and revert the README recovery section.
