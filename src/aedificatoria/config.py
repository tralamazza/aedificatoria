"""Configuration and repository-relative path matching."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


_DEFAULT_EXCLUDES = (
    ".git/**",
    ".venv/**",
    "venv/**",
    "node_modules/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "dist/**",
    "build/**",
    "target/**",
    "coverage/**",
    ".next/**",
    ".nuxt/**",
    ".svelte-kit/**",
    ".turbo/**",
    ".cache/**",
    "out/**",
    "**/*.egg-info/**",
)

_DEFAULT_ONE_WAY_DOORS = (
    "migrations/**",
    "**/migrations/**",
    "schema/**",
    "**/schema/**",
    "api/**",
    "**/api/**",
    "auth/**",
    "**/auth/**",
    "*.proto",
    "**/*.proto",
    "**/openapi*.yaml",
    "**/openapi*.json",
)

_DEFAULT_TEST_PATHS = (
    "tests/**",
    "**/tests/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*_test.rs",
    "**/*_tests.rs",
    "**/*.test.*",
    "**/*.spec.*",
    "**/__tests__/**",
    "**/__test__/**",
    "**/test_*.c",
    "**/*_test.c",
    "**/test_*.cc",
    "**/*_test.cc",
    "**/test_*.cpp",
    "**/*_test.cpp",
    "**/test_*.cxx",
    "**/*_test.cxx",
)

_DEFAULT_TEST_CONFIG_PATHS = (
    "conftest.py",
    "**/conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    ".coveragerc",
    "**/jest.config.*",
    "**/vitest.config.*",
    "**/.mocharc.*",
    "**/karma.conf.*",
    "**/playwright.config.*",
    "**/cypress.config.*",
    "**/jest.setup.*",
    "**/vitest.setup.*",
    "**/CTestTestfile.cmake",
)

_DEFAULT_METRIC_EXCLUDES = (
    "docs/**",
    "examples/**",
    "benchmarks/**",
)

_DEFAULT_API_PATHS = (
    "**/*.py",
    "**/*.rs",
    "**/*.js",
    "**/*.jsx",
    "**/*.mjs",
    "**/*.cjs",
    "**/*.ts",
    "**/*.tsx",
    "**/*.mts",
    "**/*.cts",
    "**/*.h",
    "**/*.hpp",
    "**/*.hh",
    "**/*.hxx",
    "**/*.ixx",
)


@dataclass(frozen=True)
class ForbiddenEdge:
    source: str
    target: str
    reason: str = ""


@dataclass(frozen=True)
class Config:
    base: str = "HEAD"
    adr_dir: str = "docs/decisions"
    source_roots: tuple[str, ...] = ("src", ".")
    exclude: tuple[str, ...] = _DEFAULT_EXCLUDES
    one_way_doors: tuple[str, ...] = _DEFAULT_ONE_WAY_DOORS
    test_paths: tuple[str, ...] = _DEFAULT_TEST_PATHS
    test_config_paths: tuple[str, ...] = _DEFAULT_TEST_CONFIG_PATHS
    api_paths: tuple[str, ...] = _DEFAULT_API_PATHS
    metric_excludes: tuple[str, ...] = _DEFAULT_METRIC_EXCLUDES
    allow_new_dependencies: tuple[str, ...] = ()
    forbidden_edges: tuple[ForbiddenEdge, ...] = ()
    baseline_path: str = "aed-baseline.json"
    max_changed_files: int = 25
    max_changed_lines: int = 800
    max_complexity: int = 15
    complexity_delta: int = 5
    max_baseline_complexity_growth: int = 0
    max_baseline_api_growth: int = 0
    max_baseline_dependency_growth: int = 0
    max_baseline_edge_growth: int = 0
    cargo_advisory: bool = True
    require_adr_for_large_change: bool = True
    block_removed_tests: bool = True
    block_removed_asserts: bool = True
    block_new_skips: bool = True
    block_breaking_api: bool = True
    fail_on_warn: bool = False

    def is_excluded(self, path: str) -> bool:
        return matches(path, self.exclude)

    def is_test(self, path: str) -> bool:
        return matches(path, self.test_paths) or path.rsplit("/", 1)[-1] == "conftest.py"

    def is_api(self, path: str) -> bool:
        return (
            not self.is_private(path)
            and matches(path, self.api_paths)
            and not self.is_test(path)
        )

    def is_private(self, path: str) -> bool:
        return any(
            part.startswith("_") and part != "__init__.py"
            for part in path.replace("\\", "/").split("/")
            if part
        )

    def is_metric_excluded(self, path: str) -> bool:
        return matches(path, self.metric_excludes)

    def is_one_way_door(self, path: str) -> bool:
        return matches(path, self.one_way_doors)

    def is_adr(self, path: str) -> bool:
        directory = self.adr_dir.strip("/")
        return bool(directory) and path.startswith(directory + "/") and path.endswith(".md")

    def is_test_config(self, path: str) -> bool:
        return matches(path, self.test_config_paths)

    def package_of(self, path: str) -> str:
        """Return the top-level package for a repository-relative Python path.

        With source_roots=("src", "."), `src/billing/core.py` and
        `billing/core.py` both become `billing`; `utils.py` becomes `utils`.
        """
        posix = path.replace("\\", "/").lstrip("/")
        for root in self.source_roots:
            root = root.strip("/")
            if root in ("", "."):
                rel = posix
            elif posix == root:
                rel = ""
            elif posix.startswith(root + "/"):
                rel = posix[len(root) + 1 :]
            else:
                continue
            parts = [part for part in rel.split("/") if part]
            if not parts:
                return ""
            if len(parts) == 1:
                stem = Path(parts[0]).stem
                return "" if stem == "__init__" else stem
            return parts[0]
        return ""


@lru_cache(maxsize=4096)
def _segment_match(pattern: str, text: str) -> bool:
    @lru_cache(maxsize=None)
    def walk(pattern_index: int, text_index: int) -> bool:
        if pattern_index == len(pattern):
            return text_index == len(text)
        char = pattern[pattern_index]
        if char == "*":
            return walk(pattern_index + 1, text_index) or (
                text_index < len(text) and walk(pattern_index, text_index + 1)
            )
        if text_index == len(text):
            return False
        return (char == "?" or char == text[text_index]) and walk(
            pattern_index + 1, text_index + 1
        )

    return walk(0, 0)


@lru_cache(maxsize=4096)
def _glob_match(pattern: str, path: str) -> bool:
    pattern_parts = tuple(part for part in pattern.split("/") if part)
    path_parts = tuple(part for part in path.split("/") if part)

    @lru_cache(maxsize=None)
    def walk(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return walk(pattern_index + 1, path_index) or (
                path_index < len(path_parts) and walk(pattern_index, path_index + 1)
            )
        if path_index == len(path_parts):
            return False
        return _segment_match(part, path_parts[path_index]) and walk(
            pattern_index + 1, path_index + 1
        )

    return walk(0, 0)


def _complex_glob(pattern: str) -> bool:
    return len(pattern) > 48 or pattern.count("*") + pattern.count("?") > 4


@lru_cache(maxsize=512)
def _regex_glob(pattern: str) -> re.Pattern[str]:
    out: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                if pattern[index : index + 3] == "**/":
                    out.append("(?:.*/)?")
                    index += 3
                else:
                    out.append(".*")
                    index += 2
            else:
                out.append("[^/]*")
                index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    out.append("$")
    return re.compile("".join(out))


@lru_cache(maxsize=8192)
def _matches_cached(path: str, patterns: tuple[str, ...]) -> bool:
    posix = path.replace("\\", "/")
    if posix.startswith("./"):
        posix = posix[2:]
    posix = posix.lstrip("/")
    basename = posix.rsplit("/", 1)[-1]
    for raw in patterns:
        pattern = raw.strip()
        if not pattern:
            continue
        if "/" not in pattern:
            if _complex_glob(pattern):
                matched = _segment_match(pattern, basename)
            else:
                matched = fnmatch.fnmatchcase(basename, pattern)
        elif _complex_glob(pattern):
            matched = _glob_match(pattern, posix)
        else:
            matched = _regex_glob(pattern).match(posix) is not None
        if matched:
            return True
    return False


def matches(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    """Match a repository-relative path against gitignore-like patterns.

    A pattern without `/` matches the basename at any depth; a pattern with
    `/` is anchored at the repository root. `**/` matches zero or more dirs.
    """
    return _matches_cached(path, tuple(patterns))


def _as_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _as_bool(value: Any, default: bool) -> bool:
    return bool(value) if value is not None else default


def _as_int(value: Any, default: int) -> int:
    return int(value) if value is not None else default


def load_config(repo: Path, path: str | Path | None = None) -> Config:
    if path is None:
        candidate = repo / "aed.toml"
    else:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = repo / candidate
    if not candidate.exists():
        return Config()
    if candidate.is_symlink():
        raise ConfigError(f"configuration must not be a symlink: {path or 'aed.toml'}")
    try:
        if candidate.stat().st_size > 1024 * 1024:
            raise ConfigError("configuration exceeds 1 MiB")
        raw = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read {path or 'aed.toml'}: {exc}") from exc
    aed: dict[str, Any] = raw.get("aed", {})
    thresholds: dict[str, Any] = raw.get("thresholds", {})

    edges: list[ForbiddenEdge] = []
    for item in aed.get("forbidden_edges", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("from", "")).strip()
        target = str(item.get("to", "")).strip()
        if source and target:
            edges.append(ForbiddenEdge(source, target, str(item.get("reason", ""))))

    return Config(
        base=str(aed.get("base", "HEAD")),
        adr_dir=str(aed.get("adr_dir", "docs/decisions")).strip("/"),
        source_roots=_as_tuple(aed.get("source_roots"), ("src", ".")),
        exclude=_as_tuple(aed.get("exclude"), _DEFAULT_EXCLUDES),
        one_way_doors=_as_tuple(aed.get("one_way_doors"), _DEFAULT_ONE_WAY_DOORS),
        test_paths=_as_tuple(aed.get("test_paths"), _DEFAULT_TEST_PATHS),
        test_config_paths=_as_tuple(aed.get("test_config_paths"), _DEFAULT_TEST_CONFIG_PATHS),
        api_paths=_as_tuple(aed.get("api_paths"), _DEFAULT_API_PATHS),
        metric_excludes=_as_tuple(aed.get("metric_excludes"), _DEFAULT_METRIC_EXCLUDES),
        allow_new_dependencies=_as_tuple(aed.get("allow_new_dependencies"), ()),
        forbidden_edges=tuple(edges),
        baseline_path=str(aed.get("baseline_path", "aed-baseline.json")),
        max_changed_files=_as_int(thresholds.get("max_changed_files", aed.get("max_changed_files")), 25),
        max_changed_lines=_as_int(thresholds.get("max_changed_lines", aed.get("max_changed_lines")), 800),
        max_complexity=_as_int(thresholds.get("max_complexity", aed.get("max_complexity")), 15),
        complexity_delta=_as_int(thresholds.get("complexity_delta", aed.get("complexity_delta")), 5),
        max_baseline_complexity_growth=_as_int(
            thresholds.get("max_baseline_complexity_growth", aed.get("max_baseline_complexity_growth")), 0
        ),
        max_baseline_api_growth=_as_int(
            thresholds.get("max_baseline_api_growth", aed.get("max_baseline_api_growth")), 0
        ),
        max_baseline_dependency_growth=_as_int(
            thresholds.get("max_baseline_dependency_growth", aed.get("max_baseline_dependency_growth")), 0
        ),
        max_baseline_edge_growth=_as_int(
            thresholds.get("max_baseline_edge_growth", aed.get("max_baseline_edge_growth")), 0
        ),
        cargo_advisory=_as_bool(aed.get("cargo_advisory"), True),
        require_adr_for_large_change=_as_bool(aed.get("require_adr_for_large_change"), True),
        block_removed_tests=_as_bool(aed.get("block_removed_tests"), True),
        block_removed_asserts=_as_bool(aed.get("block_removed_asserts"), True),
        block_new_skips=_as_bool(aed.get("block_new_skips"), True),
        block_breaking_api=_as_bool(aed.get("block_breaking_api"), True),
        fail_on_warn=_as_bool(aed.get("fail_on_warn"), False),
    )
