# 0035. PyPI trusted publishing

- Status: accepted
- Date: 2026-11-27

## Context

The package already has a Hatchling build, a console entry point (`aed`), and
pinned runtime dependencies, but no reproducible release path. Users need a
simple install command before Homebrew, Docker, or standalone binaries are worth
maintaining.

## Decision

Publish the `aedificatoria` distribution to PyPI with the existing `aed` entry
point. A tag matching `v<project.version>` triggers `.github/workflows/release.yml`:

1. Verify the tag equals the `pyproject.toml` version.
2. Build the sdist and wheel.
3. Install the wheel into a fresh virtualenv and smoke-test `aed --version` and
   `aed doctor`.
4. Upload the artifacts.
5. Publish through PyPI Trusted Publishing (`pypa/gh-action-pypi-publish`) in
   the `pypi` environment with `id-token: write`; no long-lived PyPI token.

The README documents `uv tool install`, `pipx install`, `pip install`, and
one-off `uvx`/`pipx run` commands.

## Consequences

Publishing requires a one-time PyPI pending-publisher configuration for the
GitHub repository, workflow `release.yml`, and `pypi` environment. Release tags
must match the static project version. PyPI availability and native
tree-sitter wheel coverage become part of the release compatibility surface.

For predictable CI and agent runs, pin a version:
`uvx --from aedificatoria==<version> aed check --staged`.

## Alternatives considered

- GitHub Releases only. Rejected as the primary channel: users still need a
  manual download/install step; PyPI is the standard Python path.
- Homebrew first. Deferred: macOS-only and needs a separate tap.
- Standalone binaries first. Deferred: tree-sitter native dependencies require
  per-OS/CPU builds and ongoing packaging maintenance.
- API tokens stored as GitHub secrets. Rejected in favor of trusted publishing.

## Rollback

Remove the release workflow, `[project.urls]`/metadata additions, and the PyPI
install documentation. Existing packages on PyPI remain; publish a final release
or yank versions there if needed.
