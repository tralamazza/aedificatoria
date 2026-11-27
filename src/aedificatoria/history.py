"""Audit first-parent history and rank commits by metric regressions.

This is a report, not a gate: it reconstructs each commit in a temporary
worktree, computes the same metrics as the persistent baseline, and compares
consecutive commits with zero growth caps so regressions surface even when the
current baseline has already accepted them.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Any, Generator

from . import baseline
from .config import Config
from .git import (
    base_worktree,
    commit_exists,
    commit_parents,
    diff_stats,
    history_commits,
)
from .model import Finding
from .pyast import strongly_connected

_SQUASH_PR = re.compile(r"\(#(\d+)\)\s*$")


@dataclass(frozen=True)
class HistoryPoint:
    commit: str
    date: str
    subject: str
    parents: int
    kind: str
    pr: str
    files: int
    lines: int
    complexity: int
    api: int
    dependencies: int
    edges: int
    tests: int
    assertions: int
    regressions: tuple[Finding, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "date": self.date,
            "subject": self.subject,
            "parents": self.parents,
            "kind": self.kind,
            "pr": self.pr,
            "churn": {"files": self.files, "lines": self.lines},
            "metrics": {
                "complexity": self.complexity,
                "api": self.api,
                "dependencies": self.dependencies,
                "edges": self.edges,
                "tests": self.tests,
                "assertions": self.assertions,
            },
            "regressions": [item.as_dict() for item in self.regressions],
        }


def _total(values: object) -> int:
    mapping = values if isinstance(values, dict) else {}
    return sum(
        score
        for functions in mapping.values()
        for score in (functions if isinstance(functions, dict) else {}).values()
        if isinstance(score, int)
    )


def _count(values: object) -> int:
    mapping = values if isinstance(values, dict) else {}
    return sum(len(items) for items in mapping.values() if isinstance(items, list))


def _test_counts(values: object) -> tuple[int, int]:
    mapping = values if isinstance(values, dict) else {}
    tests = 0
    assertions = 0
    for counts in mapping.values():
        if not isinstance(counts, dict):
            continue
        tests += len(counts)
        assertions += sum(count for count in counts.values() if isinstance(count, int))
    return tests, assertions


def _summary(metrics: dict[str, Any]) -> dict[str, int]:
    tests, assertions = _test_counts(metrics.get("tests"))
    return {
        "complexity": _total(metrics.get("complexity")),
        "api": _count(metrics.get("api")),
        "dependencies": len(metrics.get("dependencies") or []),
        "edges": len(metrics.get("edges") or []),
        "tests": tests,
        "assertions": assertions,
    }


def _cycles(metrics: dict[str, Any]) -> set[tuple[str, ...]]:
    edges = {
        tuple(edge)
        for edge in metrics.get("edges") or []
        if isinstance(edge, list) and len(edge) == 2
    }
    nodes = {node for edge in edges for node in edge}
    return set(strongly_connected(edges, nodes))


def _relabel(item: Finding, commit: str, subject: str) -> Finding:
    message = item.message.replace("since baseline", "in this commit")
    return Finding(
        "history",
        item.severity,
        message,
        path=item.path,
        line=item.line,
        evidence=f"{commit[:12]} {subject}",
    )


def _kind(parents: tuple[str, ...], subject: str) -> tuple[str, str]:
    if len(parents) > 1:
        return "merge", ""
    match = _SQUASH_PR.search(subject)
    if match:
        return "squash", match.group(1)
    return "commit", ""


def _atomic_finding(
    parents: tuple[str, ...],
    kind: str,
    pr: str,
    files: int,
    lines: int,
    config: Config,
    commit: str,
    subject: str,
) -> list[Finding]:
    if not parents or len(parents) > 1:
        return []
    over_files = config.max_changed_files > 0 and files > config.max_changed_files
    over_lines = config.max_changed_lines > 0 and lines > config.max_changed_lines
    if not (over_files or over_lines):
        return []
    label = f"squash #{pr}" if kind == "squash" else "single commit"
    return [
        Finding(
            "history",
            "warn",
            f"large atomic {label}: {files} files, {lines} changed lines; "
            "internal commits are unavailable",
            evidence=f"{commit[:12]} {subject}",
        )
    ]


def _regressions(
    previous: dict[str, Any],
    current: dict[str, Any],
    config: Config,
    commit: str,
    subject: str,
) -> list[Finding]:
    strict = replace(
        config,
        max_baseline_complexity_growth=0,
        max_baseline_api_growth=0,
        max_baseline_dependency_growth=0,
        max_baseline_edge_growth=0,
    )
    findings = [
        _relabel(item, commit, subject)
        for item in baseline.compare(previous, current, strict)
    ]
    for component in sorted(_cycles(current) - _cycles(previous)):
        findings.append(
            Finding(
                "history",
                "error",
                f"new package cycle: {', '.join(component)}",
                evidence=f"{commit[:12]} {subject}",
            )
        )
    return findings


def _point(
    commit: str,
    date: str,
    subject: str,
    parents: tuple[str, ...],
    files: int,
    lines: int,
    metrics: dict[str, Any],
    regressions: list[Finding],
) -> HistoryPoint:
    summary = _summary(metrics)
    kind, pr = _kind(parents, subject)
    return HistoryPoint(
        commit=commit,
        date=date,
        subject=subject,
        parents=len(parents),
        kind=kind,
        pr=pr,
        files=files,
        lines=lines,
        complexity=summary["complexity"],
        api=summary["api"],
        dependencies=summary["dependencies"],
        edges=summary["edges"],
        tests=summary["tests"],
        assertions=summary["assertions"],
        regressions=tuple(regressions),
    )


def evaluate(
    repo: Path,
    config: Config,
    limit: int = 20,
    since: str | None = None,
    head: str = "HEAD",
) -> list[HistoryPoint]:
    points: list[HistoryPoint] = []
    previous: dict[str, Any] | None = None
    for commit, date, subject in history_commits(repo, limit, since, head):
        parents = commit_parents(repo, commit)
        base = parents[0] if parents else None
        files, lines = diff_stats(repo, base, commit)
        with base_worktree(repo, commit) as tree:
            metrics = baseline.collect(tree, config)
        regressions = (
            []
            if previous is None
            else _regressions(previous, metrics, config, commit, subject)
        )
        kind, pr = _kind(parents, subject)
        regressions.extend(
            _atomic_finding(parents, kind, pr, files, lines, config, commit, subject)
        )
        points.append(_point(commit, date, subject, parents, files, lines, metrics, regressions))
        previous = metrics
    return points


@contextmanager
def _materialize(repo: Path, sha: str, client: Any) -> Generator[Path, None, None]:
    if commit_exists(repo, sha):
        with base_worktree(repo, sha) as tree:
            yield tree
    else:
        with client.tarball(sha) as tree:
            yield tree


def evaluate_forge(
    repo: Path,
    config: Config,
    number: int,
    limit: int,
    client: Any,
) -> list[HistoryPoint]:
    points: list[HistoryPoint] = []
    previous: dict[str, Any] | None = None
    for item in client.pr_commits(number, limit):
        with _materialize(repo, item.sha, client) as root:
            metrics = baseline.collect(root, config)
        regressions = (
            []
            if previous is None
            else _regressions(previous, metrics, config, item.sha, item.subject)
        )
        points.append(
            _point(
                item.sha,
                item.date,
                item.subject,
                item.parents,
                item.files,
                item.lines,
                metrics,
                regressions,
            )
        )
        previous = metrics
    return points


def worst(points: list[HistoryPoint], limit: int = 5) -> list[HistoryPoint]:
    ranked = sorted(
        (point for point in points if point.regressions),
        key=lambda point: (-len(point.regressions), point.commit),
    )
    return ranked[:limit]


def _delta(value: int, previous: int | None) -> str:
    if previous is None:
        return f"{value}"
    return f"{value} ({value - previous:+d})"


def render_text(points: list[HistoryPoint], header: str = "first-parent commits") -> str:
    if not points:
        return f"AEDIFICATORIA HISTORY  {header}\n\nno commits"
    lines = [
        f"AEDIFICATORIA HISTORY  {header}",
        "",
        "commit    date        kind        complexity      api       deps  edges  tests  assert  churn       regr",
    ]
    previous: HistoryPoint | None = None
    for point in points:
        kind = f"squash#{point.pr}" if point.kind == "squash" else point.kind
        lines.append(
            f"{point.commit[:8]}  {point.date[:10]}  {kind:<10}  "
            f"{_delta(point.complexity, previous.complexity if previous else None):>13}  "
            f"{_delta(point.api, previous.api if previous else None):>8}  "
            f"{_delta(point.dependencies, previous.dependencies if previous else None):>5}  "
            f"{point.edges:>5}  {point.tests:>5}  {point.assertions:>6}  "
            f"{point.files:>4}f/{point.lines:<5}l  {len(point.regressions):>4}"
        )
        previous = point
    ranked = worst(points)
    if ranked:
        lines.append("")
        lines.append("WHERE IT WENT BAD")
        for point in ranked:
            marker = f" [squash #{point.pr}]" if point.kind == "squash" else ""
            lines.append(
                f"{point.commit[:8]} {point.subject}{marker} — "
                f"{point.files}f/{point.lines}l — {len(point.regressions)} regression(s)"
            )
            for item in point.regressions[:4]:
                location = f" ({item.path})" if item.path else ""
                lines.append(f"  [{item.severity}] {item.message}{location}")
            if len(point.regressions) > 4:
                lines.append(f"  ... +{len(point.regressions) - 4} more")
    return "\n".join(lines)


def json_payload(
    points: list[HistoryPoint], source: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool": "aedificatoria",
        "kind": "history",
        "commits": [point.as_dict() for point in points],
        "worst": [point.as_dict() for point in worst(points)],
    }
    if source is not None:
        payload["source"] = source
    return payload
