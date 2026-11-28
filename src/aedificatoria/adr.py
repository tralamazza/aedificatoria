"""Architecture decision record creation and project initialization."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from .config import Config
from .paths import ConfinedPathError, confined

CONFIG_TEMPLATE = """# Aedificatoria configuration.

[aed]
base = "HEAD"
adr_dir = "docs/decisions"
source_roots = ["src", "."]

# Paths whose change requires an architecture decision record.
# one_way_doors = [
#   "migrations/**", "**/migrations/**",
#   "schema/**", "**/schema/**",
#   "api/**", "**/api/**",
#   "auth/**", "**/auth/**",
#   "*.proto", "**/*.proto",
#   "**/openapi*.yaml", "**/openapi*.json",
# ]

# test_paths = [
#   "tests/**", "**/tests/**", "**/test_*.py", "**/*_test.py",
#   "**/*_test.rs", "**/*_tests.rs", "**/*.test.*", "**/*.spec.*",
#   "**/__tests__/**", "**/__test__/**",
#   "test/**", "**/test/**",
#   "**/test_*.c", "**/*_test.c", "**/test-*.c", "**/test.c", "**/test.cpp",
#   "**/test_*.cc", "**/*_test.cc", "**/test-*.cc",
#   "**/test_*.cpp", "**/*_test.cpp", "**/test-*.cpp",
#   "**/test_*.cxx", "**/*_test.cxx", "**/test-*.cxx",
# ]
# test_config_paths = [
#   "conftest.py", "**/conftest.py", "pytest.ini", "tox.ini", "setup.cfg",
#   ".coveragerc", "**/jest.config.*", "**/vitest.config.*", "**/.mocharc.*",
#   "**/karma.conf.*", "**/playwright.config.*", "**/cypress.config.*",
#   "**/jest.setup.*", "**/vitest.setup.*", "**/CTestTestfile.cmake",
# ]
# api_paths = ["**/*.py", "**/*.rs", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.mjs", "**/*.cjs", "**/*.mts", "**/*.cts", "**/*.h", "**/*.hpp", "**/*.hh", "**/*.hxx", "**/*.ixx"]
# metric_excludes = ["docs/**", "examples/**", "benchmarks/**"]
# allow_new_dependencies = []
baseline_path = "aed-baseline.json"
cargo_advisory = true
require_adr_for_large_change = true
block_removed_tests = true
block_removed_asserts = true
block_new_skips = true
block_breaking_api = true
# Exit 1 for a review verdict (warnings). Default: false (review exits 0).
fail_on_warn = false

[thresholds]
max_changed_files = 25
max_changed_lines = 800
max_complexity = 15
complexity_delta = 5
max_baseline_complexity_growth = 0
max_baseline_api_growth = 0
max_baseline_dependency_growth = 0
max_baseline_edge_growth = 0

# [[aed.forbidden_edges]]
# from = "api"
# to = "db"
# reason = "API must go through the service layer, not the database directly."
"""

FIRST_ADR = """# 0001. Record architecture decisions

- Status: accepted
- Date: {date}

## Context

Coding agents make locally reasonable, confident decisions whose architectural
cost appears later: coupling, cycles, leaky interfaces, and features that
require edits across many modules. The rationale for those decisions is usually
lost when the agent's context is compacted.

## Decision

Record every irreversible or high-blast-radius decision in a numbered ADR under
`docs/decisions/`. Each record states the context, the decision, the
alternatives, the consequences, the invariants, and the rollback path.
One-way-door changes without a record fail `aed check`.

## Alternatives considered

- Rely on the agent's chat history. Rejected: it is lossy and not reviewable.
- Rely on code comments alone. Rejected: they do not capture alternatives or rollback.

## Consequences

Decisions become durable, reviewable artifacts. The guard can compare the
current diff against them instead of relying on model confidence.

## Invariants

- A one-way-door change has an accepted or proposed ADR.
- Tests are not deleted, weakened, or skipped to make a change pass.
- New package cycles and forbidden dependency edges fail the check.

## Rollback

Delete the proposed ADR; for an accepted decision, write a superseding ADR.
"""


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "adr"


def _next_number(directory: Path) -> int:
    highest = 0
    for path in directory.glob("*.md"):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def create_adr(
    root: Path,
    title: str,
    *,
    status: str = "proposed",
    config: Config | None = None,
) -> Path:
    cfg = config or Config()
    try:
        directory = confined(root, cfg.adr_dir)
    except ConfinedPathError as exc:
        raise ValueError(f"invalid adr_dir: {exc}") from exc
    raw = root / cfg.adr_dir
    if raw.is_symlink():
        raise ValueError(f"adr_dir must not be a symlink: {cfg.adr_dir}")
    directory.mkdir(parents=True, exist_ok=True)
    number = _next_number(directory)
    path = directory / f"{number:04d}-{slugify(title)}.md"
    if path.is_symlink():
        raise ValueError(f"ADR path must not be a symlink: {path.name}")
    date = _dt.date.today().isoformat()
    path.write_text(
        f"""# {number:04d}. {title}

- Status: {status}
- Date: {date}

## Context

...

## Decision

...

## Alternatives considered

...

## Consequences

...

## Invariants

- ...

## Rollback

...
""",
        encoding="utf-8",
    )
    return path


def init_project(root: Path) -> tuple[list[Path], list[Path]]:
    """Create aed.toml and the first ADR. Returns (created, already_present)."""
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    existing: list[Path] = []

    try:
        config_path = confined(root, "aed.toml")
        adr_dir = confined(root, "docs/decisions")
    except ConfinedPathError as exc:
        raise ValueError(str(exc)) from exc
    if (root / "aed.toml").is_symlink() or (root / "docs/decisions").is_symlink():
        raise ValueError("init paths must not be symlinks")
    if config_path.exists():
        existing.append(config_path)
    else:
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        created.append(config_path)

    adr_dir.mkdir(parents=True, exist_ok=True)
    first = adr_dir / "0001-record-architecture-decisions.md"
    if first.is_symlink():
        raise ValueError("first ADR path must not be a symlink")
    existing_adrs = sorted(adr_dir.glob("*.md"))
    if existing_adrs:
        existing.extend(existing_adrs[:1])
    else:
        first.write_text(FIRST_ADR.format(date=_dt.date.today().isoformat()), encoding="utf-8")
        created.append(first)
    return created, existing


WORKFLOW_PATH = ".github/workflows/aed.yml"

WORKFLOW_TEMPLATE = """# Generated by `aed init --github`.
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
      - uses: tralamazza/aedificatoria@v{version}
        # Optional:
        # with:
        #   strict: true      # fail on warnings as well as errors
        #   enforce: false    # shadow mode: report without failing
"""


def init_github_workflow(root: Path, version: str) -> tuple[Path, bool]:
    """Create the GitHub Actions workflow. Returns (path, already_present)."""
    try:
        target = confined(root, WORKFLOW_PATH)
    except ConfinedPathError as exc:
        raise ValueError(str(exc)) from exc
    if target.is_symlink():
        raise ValueError("workflow path must not be a symlink")
    if target.exists():
        return target, True
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(WORKFLOW_TEMPLATE.format(version=version), encoding="utf-8")
    return target, False
