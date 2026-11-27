"""Persistent metric baseline: monotonic ratchets across commits.

The baseline is a committed JSON snapshot of per-file complexity, public API
symbols, dependencies, package edges, per-test assertion counts, and
parameterized test-case counts. `aed check` compares the working tree against it,
so small increases cannot accumulate commit by commit. Refreshing the baseline is
a deliberate, ADR-gated change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import languages
from .config import Config
from .git import tracked_source_files
from .model import Finding

VERSION = 3
_EVIDENCE = "Run `aed baseline` with an accepted ADR to reset the ratchet."


class BaselineError(RuntimeError):
    pass


def _source_files(root: Path, config: Config) -> list[str]:
    return tracked_source_files(root, config, languages.extensions())


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _collect_file(root: Path, rel: str, config: Config, data: dict[str, Any]) -> None:
    if config.is_metric_excluded(rel):
        return
    adapter = languages.get(rel)
    if adapter is None:
        return
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    tree = adapter.parse_text(rel, text)
    if tree is None:
        return
    info = adapter.test_info(tree)
    if info.counts:
        data["tests"][rel] = dict(sorted(info.counts.items()))
    if info.cases:
        data["test_cases"][rel] = dict(sorted(info.cases.items()))
    if config.is_test(rel):
        return
    scores = adapter.complexity(tree)
    if scores:
        data["complexity"][rel] = dict(sorted(scores.items()))
    if config.is_api(rel):
        surface = adapter.public_api(tree)
        if surface:
            data["api"][rel] = dict(sorted(surface.items()))


def _dependencies(root: Path, config: Config) -> list[str]:
    imports = languages.project_imports(root, config)
    local = {name: languages.local_packages(root, config, name) for name in languages.names()}
    found: set[str] = set()
    for module, (_path, _line, language) in imports.items():
        if module in local.get(language, set()) or languages.is_stdlib(language, module):
            continue
        found.add(module)
    return sorted(found)


def collect(root: Path, config: Config) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": VERSION,
        "complexity": {},
        "api": {},
        "dependencies": [],
        "edges": [],
        "tests": {},
        "test_cases": {},
    }
    for rel in _source_files(root, config):
        _collect_file(root, rel, config, data)
    data["dependencies"] = _dependencies(root, config)
    edges, _packages = languages.package_graph(root, config)
    data["edges"] = [list(edge) for edge in sorted(edges)]
    return data


def load(root: Path, path: str) -> dict[str, Any] | None:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(str(exc)) from exc
    if not isinstance(data, dict) or data.get("version") not in {1, 2, VERSION}:
        raise BaselineError(f"unsupported baseline format (expected version {VERSION})")
    return data


def write(root: Path, path: str, data: dict[str, Any]) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return candidate


def _nested_total(mapping: dict[str, Any]) -> int:
    return sum(
        score
        for values in mapping.values()
        for score in _mapping(values).values()
        if isinstance(score, int)
    )


def _complexity_findings(
    baseline: dict[str, Any], head: dict[str, Any], config: Config
) -> list[Finding]:
    base_map = _mapping(baseline.get("complexity"))
    head_map = _mapping(head.get("complexity"))
    findings: list[Finding] = []
    for file, functions in base_map.items():
        current = _mapping(head_map.get(file))
        for name, score in _mapping(functions).items():
            value = current.get(name)
            if isinstance(score, int) and isinstance(value, int) and value > score:
                findings.append(
                    Finding(
                        "baseline",
                        "error",
                        f"`{name}` complexity grew {score} -> {value} since baseline",
                        path=file,
                        evidence=_EVIDENCE,
                    )
                )
    base_total = _nested_total(base_map)
    head_total = _nested_total(head_map)
    allowed = base_total + max(0, config.max_baseline_complexity_growth)
    if head_total > allowed:
        findings.append(
            Finding(
                "baseline",
                "error",
                f"total complexity grew {base_total} -> {head_total} since baseline "
                f"(allowed +{max(0, config.max_baseline_complexity_growth)})",
                evidence=_EVIDENCE,
            )
        )
    return findings


def _api_symbols(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(name): str(signature) for name, signature in value.items()}
    if isinstance(value, list):
        return {str(name): "" for name in value}
    return {}


def _api_findings(baseline: dict[str, Any], head: dict[str, Any], config: Config) -> list[Finding]:
    base_map = _mapping(baseline.get("api"))
    head_map = _mapping(head.get("api"))
    findings: list[Finding] = []
    for file, symbols in base_map.items():
        base_symbols = _api_symbols(symbols)
        head_symbols = _api_symbols(head_map.get(file))
        for symbol, signature in base_symbols.items():
            if symbol not in head_symbols:
                findings.append(
                    Finding(
                        "baseline",
                        "error",
                        f"public symbol `{symbol}` removed since baseline",
                        path=file,
                        evidence=_EVIDENCE,
                    )
                )
            elif signature and head_symbols[symbol] and signature != head_symbols[symbol]:
                findings.append(
                    Finding(
                        "baseline",
                        "error",
                        f"public symbol `{symbol}` signature changed "
                        f"{signature} -> {head_symbols[symbol]} since baseline",
                        path=file,
                        evidence=_EVIDENCE,
                    )
                )
    base_total = sum(len(_api_symbols(value)) for value in base_map.values())
    head_total = sum(len(_api_symbols(value)) for value in head_map.values())
    if head_total > base_total + max(0, config.max_baseline_api_growth):
        findings.append(
            Finding(
                "baseline",
                "error",
                f"public API grew {base_total} -> {head_total} symbols since baseline "
                f"(allowed +{max(0, config.max_baseline_api_growth)})",
                evidence=_EVIDENCE,
            )
        )
    return findings


def _dependency_findings(
    baseline: dict[str, Any], head: dict[str, Any], config: Config
) -> list[Finding]:
    base = set(baseline.get("dependencies") or [])
    current = set(head.get("dependencies") or [])
    added = sorted(current - base)
    if len(added) <= max(0, config.max_baseline_dependency_growth):
        return []
    listing = ", ".join(added[:8])
    if len(added) > 8:
        listing += f", +{len(added) - 8} more"
    return [
        Finding(
            "baseline",
            "error",
            f"new dependencies since baseline: {listing}",
            evidence=_EVIDENCE,
        )
    ]


def _edge_findings(baseline: dict[str, Any], head: dict[str, Any], config: Config) -> list[Finding]:
    base = {tuple(edge) for edge in baseline.get("edges") or [] if isinstance(edge, list) and len(edge) == 2}
    current = {tuple(edge) for edge in head.get("edges") or [] if isinstance(edge, list) and len(edge) == 2}
    added = sorted(current - base)
    if len(added) <= max(0, config.max_baseline_edge_growth):
        return []
    listing = ", ".join(f"{source} -> {target}" for source, target in added[:8])
    if len(added) > 8:
        listing += f", +{len(added) - 8} more"
    return [
        Finding(
            "baseline",
            "error",
            f"new package edges since baseline: {listing}",
            evidence=_EVIDENCE,
        )
    ]


def _test_findings(baseline: dict[str, Any], head: dict[str, Any]) -> list[Finding]:
    base_map = _mapping(baseline.get("tests"))
    head_map = _mapping(head.get("tests"))
    findings: list[Finding] = []
    for file, tests in base_map.items():
        current = _mapping(head_map.get(file))
        for name, count in _mapping(tests).items():
            if name not in current:
                findings.append(
                    Finding(
                        "baseline",
                        "error",
                        f"test `{name}` removed since baseline",
                        path=file,
                        evidence=_EVIDENCE,
                    )
                )
            elif isinstance(count, int) and isinstance(current[name], int) and current[name] < count:
                findings.append(
                    Finding(
                        "baseline",
                        "error",
                        f"test `{name}` assertions are {current[name]} since baseline, was {count}",
                        path=file,
                        evidence=_EVIDENCE,
                    )
                )
    return findings


def _test_case_findings(baseline: dict[str, Any], head: dict[str, Any]) -> list[Finding]:
    base_map = _mapping(baseline.get("test_cases"))
    head_map = _mapping(head.get("test_cases"))
    findings: list[Finding] = []
    for file, cases in base_map.items():
        current = _mapping(head_map.get(file))
        for name, count in _mapping(cases).items():
            if not isinstance(count, int) or name not in current:
                continue
            now = current[name]
            if isinstance(now, int) and now < count:
                findings.append(
                    Finding(
                        "baseline",
                        "error",
                        f"test `{name}` parameterized cases are {now} since baseline, was {count}",
                        path=file,
                        evidence=_EVIDENCE,
                    )
                )
    return findings


def compare(
    baseline: dict[str, Any], head: dict[str, Any], config: Config
) -> list[Finding]:
    findings = _complexity_findings(baseline, head, config)
    findings.extend(_api_findings(baseline, head, config))
    findings.extend(_dependency_findings(baseline, head, config))
    findings.extend(_edge_findings(baseline, head, config))
    findings.extend(_test_findings(baseline, head))
    findings.extend(_test_case_findings(baseline, head))
    return findings
