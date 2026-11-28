# GitHub Action

`aed check` as a composite action: it resolves the event base, fetches only that
commit, runs the guard once, and renders GitHub-native output.

## Quickstart

```yaml
name: aed

on:
  pull_request:
  merge_group:

permissions:
  contents: read

jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: tralamazza/aedificatoria@v0.1.7
```

No `setup-python`, no `fetch-depth: 0`, no base-resolution shell. `aed init
--github` writes this file for you and pins the installed release.

Pin the release tag or a full commit SHA. There is no moving major tag; tags are
immutable because `v*` is protected.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `version` | action's package version | PyPI version to install. `source` installs this action's checkout (unreleased refs). |
| `base` | event base | Explicit base revision or SHA. Fails if unreachable. |
| `path` | `.` | Repository or project directory. |
| `config` | `aed.toml` when present | Config path. An explicit path must exist. |
| `strict` | `false` | Exit `1` when the verdict is `review`. |
| `enforce` | `true` | `false` runs in shadow mode: findings are reported, the step succeeds. |
| `annotations` | `true` | Emit `::error`/`::warning`/`::notice` annotations. |
| `summary` | `true` | Append the Markdown report to `$GITHUB_STEP_SUMMARY`. |
| `json-out` | `$RUNNER_TEMP/aed.json` | JSON report path. |

## Outputs

| Output | Example | Purpose |
|---|---|---|
| `verdict` | `block` | `allow`, `review`, `block`, or `error` when no report was produced. |
| `exit-code` | `2` | aed exit code before `enforce` is applied. |
| `base` | `9f1c…` | Resolved base commit. |
| `errors` / `warnings` / `infos` | `1` | Finding counts. |
| `json` | `/home/runner/work/…/aed.json` | Absolute report path. |

```yaml
- id: aed
  uses: tralamazza/aedificatoria@v0.1.7
  with:
    strict: true
- if: steps.aed.outputs.verdict == 'review'
  run: echo "warnings require review"
```

## Base resolution

The action reads `$GITHUB_EVENT_PATH` and tries, in order:

| Event | Candidate |
|---|---|
| `merge_group` | `merge_group.base_sha` |
| `pull_request` | `pull_request.base.sha` |
| `push` | `before`, unless the all-zero SHA |

If the commit is absent it runs `git fetch --depth=1 origin <sha>`, so shallow
checkouts work. A force-push or new branch falls back to a shallow fetch of the
default branch and `git merge-base`, deepening up to 1,000 commits. If no base
resolves, the step fails; it never silently diffs `HEAD` against itself. Pass
`base:` for unusual events.

`pull_request` uses the merge commit that `actions/checkout` checks out by
default. The diff against `base.sha` is still the PR change set.

## Permissions and forks

The check itself needs `contents: read`. Annotations and the job summary need no
extra permission and work on fork PRs with the read-only token. The action does
not use `pull_request_target`, does not execute repository code, and makes no
write requests.

## Shadow mode

Start with `enforce: false`. Findings appear in the log, annotations, and
summary, but the check stays green. `verdict` and `exit-code` still report the
real result, so a later step can gate on them.

```yaml
- uses: tralamazza/aedificatoria@v0.1.7
  with:
    enforce: false
```

Flip to the default after the first week. Do not combine shadow mode with
`strict: true` unless another step consumes the outputs; the step still exits
`0`.

## First setup

```sh
aed init --github --baseline
git add aed.toml aed-baseline.json docs/decisions .github/workflows/aed.yml
git commit
```

The first ADR is created as accepted. The guard reports one `guard-config`
warning for adding the baseline; it does not block by default. With
`strict: true` or `fail_on_warn = true`, mark the setup PR as intentional.

## Monorepos

`path:` selects the directory passed to `aed check --path`; because Git resolves
upward, the diff still covers the whole repository and `aed.toml` is read from
the repository root. Per-package diff scoping is not supported yet.

## Exit codes

| Exit | Verdict | Step result |
|---:|---|---|
| `0` | `allow` | success |
| `0` | `review` | success; warnings annotated |
| `1` | `review` + strict | failure |
| `2` | `block` | failure |
| `2` | CLI/config/git error | failure; no JSON report |

## Troubleshooting

- **Version not found on PyPI**: the action defaults to the version in its own
  `pyproject.toml`; pin a released tag, or use `version: source` for an
  unreleased branch.
- **Base not resolved**: pass `base:`; for new branches, ensure the job can
  fetch `origin`.
- **No annotations**: check `annotations: true` and that the token is not
  restricted from reading the repository.
- **Too many findings**: GitHub caps annotations at 10 per step and 50 per job.
  The action emits the worst first; the full set is in the job summary and
  `aed.json`.
