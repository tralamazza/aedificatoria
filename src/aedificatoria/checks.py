"""Deterministic architecture checks over a working-tree diff."""

from __future__ import annotations

import re
import sys
from collections import Counter

from .model import SEVERITY_RANK, ChangedFile, Context, Finding
from .pyast import (
    cycle_evidence,
    function_complexity,
    local_packages,
    package_edges,
    parse_text,
    project_imports,
    public_api,
    strongly_connected,
    test_info,
)


def _relevant(ctx: Context) -> list[ChangedFile]:
    return [item for item in ctx.changed if not ctx.config.is_excluded(item.path)]


def _top_dirs(files: list[ChangedFile], limit: int = 5) -> str:
    counts = Counter(item.path.split("/", 1)[0] if "/" in item.path else "(root)" for item in files)
    return ", ".join(f"{name}:{count}" for name, count in counts.most_common(limit))


def _adr_changes(ctx: Context, *, include_deleted: bool = False) -> list[ChangedFile]:
    out: list[ChangedFile] = []
    for item in _relevant(ctx):
        if not ctx.config.is_adr(item.path):
            continue
        if item.is_deleted and not include_deleted:
            continue
        if item.status in {"A", "M", "R", "C"} or (include_deleted and item.is_deleted):
            out.append(item)
    return out


def _adr_status(text: str) -> str:
    match = re.search(r"(?mi)^\s*[-*]?\s*status\s*:\s*([A-Za-z_-]+)", text)
    return match.group(1).lower() if match else ""


def _adr_valid(ctx: Context, item: ChangedFile) -> tuple[bool, str]:
    text = ctx.head_text(item.path) or ""
    lines = text.splitlines()
    has_title = any(line.startswith("# ") for line in lines)
    status = _adr_status(text)
    has_decision = re.search(r"(?mi)^##\s+Decision\b", text) is not None
    valid = has_title and status in {"accepted", "proposed"} and has_decision
    return valid, status


def check_change_amplification(ctx: Context) -> list[Finding]:
    files = _relevant(ctx)
    file_count = len(files)
    line_count = sum(item.churn for item in files)
    over_files = ctx.config.max_changed_files > 0 and file_count > ctx.config.max_changed_files
    over_lines = ctx.config.max_changed_lines > 0 and line_count > ctx.config.max_changed_lines
    if not (over_files or over_lines):
        return []

    valid_adrs = [item for item in _adr_changes(ctx) if _adr_valid(ctx, item)[0]]
    message = (
        f"large change: {file_count} files, {line_count} changed lines "
        f"(limits {ctx.config.max_changed_files} files / {ctx.config.max_changed_lines} lines)"
    )
    evidence = _top_dirs(files)
    if valid_adrs and ctx.config.require_adr_for_large_change:
        return [
            Finding(
                "change-amplification",
                "warn",
                f"{message}; justified by {valid_adrs[0].path}",
                evidence=evidence,
            )
        ]
    return [Finding("change-amplification", "error", message, evidence=evidence)]


def check_one_way_doors(ctx: Context) -> list[Finding]:
    doors = [item for item in _relevant(ctx) if ctx.config.is_one_way_door(item.path)]
    if not doors:
        return []

    adrs = _adr_changes(ctx)
    valid = [(item, _adr_valid(ctx, item)[1]) for item in adrs if _adr_valid(ctx, item)[0]]
    listing = ", ".join(item.path for item in doors[:5])
    if len(doors) > 5:
        listing += f", +{len(doors) - 5} more"

    if not valid:
        return [
            Finding(
                "one-way-door",
                "error",
                f"one-way-door change without an architecture decision record: {listing}",
                path=doors[0].path,
                evidence=(
                    f"Add a file under {ctx.config.adr_dir}/ with a title, "
                    "`- Status: accepted`, and a `## Decision` section."
                ),
            )
        ]

    findings: list[Finding] = []
    proposed = [item.path for item, status in valid if status == "proposed"]
    if proposed:
        findings.append(
            Finding(
                "one-way-door",
                "warn",
                f"one-way-door change recorded by a proposed ADR: {', '.join(proposed[:3])}",
                path=doors[0].path,
                evidence=listing,
            )
        )
    else:
        findings.append(
            Finding(
                "one-way-door",
                "info",
                f"one-way-door change recorded by ADR: {valid[0][0].path}",
                path=doors[0].path,
                evidence=listing,
            )
        )
    return findings


def check_test_tampering(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for item in _relevant(ctx):
        if item.path == "pyproject.toml":
            base_text = ctx.base_text(item.base_path) or ""
            head_text = ctx.head_text(item.path) or ""
            if "[tool.pytest" in base_text or "[tool.pytest" in head_text:
                findings.append(
                    Finding(
                        "test-config",
                        "warn",
                        "pytest configuration in pyproject.toml changed; verify it does not weaken the suite",
                        path=item.path,
                    )
                )
            continue

        if ctx.config.is_test_config(item.path):
            if item.is_deleted:
                findings.append(
                    Finding(
                        "test-config",
                        "error",
                        "test configuration deleted",
                        path=item.path,
                        evidence="Keep the verifier immutable; do not delete test configuration.",
                    )
                )
            elif item.status != "A":
                findings.append(
                    Finding(
                        "test-config",
                        "warn",
                        "test configuration changed; verify it does not weaken the suite",
                        path=item.path,
                    )
                )
            continue

        is_test = ctx.config.is_test(item.path) or ctx.config.is_test(item.base_path)
        if not is_test:
            continue

        if item.is_deleted:
            if ctx.config.block_removed_tests:
                findings.append(
                    Finding(
                        "test-tampering",
                        "error",
                        "test file deleted",
                        path=item.path,
                        evidence="A deleted test removes the verifier; restore it or record an ADR.",
                    )
                )
            continue

        if item.status == "A":
            continue

        base_tree = parse_text(ctx.base_text(item.base_path))
        head_tree = parse_text(ctx.head_text(item.path))
        if base_tree is None or head_tree is None:
            continue
        base_info = test_info(base_tree)
        head_info = test_info(head_tree)

        removed = sorted(base_info.functions - head_info.functions)
        if removed and ctx.config.block_removed_tests:
            findings.append(
                Finding(
                    "test-tampering",
                    "error",
                    f"removed test function(s): {', '.join(removed[:5])}",
                    path=item.path,
                    evidence=f"{len(removed)} of {len(base_info.functions)} tests removed",
                )
            )

        dropped = base_info.assertions - head_info.assertions
        if dropped > 0 and ctx.config.block_removed_asserts:
            findings.append(
                Finding(
                    "test-tampering",
                    "error",
                    f"test assertions decreased from {base_info.assertions} to {head_info.assertions}",
                    path=item.path,
                    evidence=f"{dropped} assertion(s) removed; tests must not be weakened to pass.",
                )
            )

        new_skips = sorted(head_info.skips - base_info.skips)
        if new_skips and ctx.config.block_new_skips:
            findings.append(
                Finding(
                    "test-tampering",
                    "error",
                    f"new skipped/xfail test(s): {', '.join(new_skips[:5])}",
                    path=item.path,
                    evidence="Unskip the test or justify the skip in an ADR.",
                )
            )

        if item.is_renamed and not (removed or dropped > 0 or new_skips):
            findings.append(
                Finding(
                    "test-tampering",
                    "info",
                    f"test file moved: {item.base_path} -> {item.path}",
                    path=item.path,
                )
            )
    return findings


def check_import_graph(ctx: Context) -> list[Finding]:
    base_edges, base_packages = package_edges(ctx.base_dir, ctx.config)
    head_edges, head_packages = package_edges(ctx.repo, ctx.config)
    base_cycles = set(strongly_connected(base_edges, base_packages))
    head_cycles = set(strongly_connected(head_edges, head_packages))

    findings: list[Finding] = []
    for component in sorted(head_cycles - base_cycles):
        findings.append(
            Finding(
                "import-cycle",
                "error",
                f"new package cycle: {', '.join(component)}",
                evidence=cycle_evidence(component, head_edges),
            )
        )

    new_edges = head_edges - base_edges
    for edge in ctx.config.forbidden_edges:
        if (edge.source, edge.target) in new_edges:
            findings.append(
                Finding(
                    "forbidden-edge",
                    "error",
                    f"forbidden dependency: {edge.source} -> {edge.target}",
                    evidence=edge.reason or "new package dependency edge",
                )
            )
    return findings


def check_complexity(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for item in _relevant(ctx):
        if not item.path.endswith(".py") or ctx.config.is_test(item.path):
            continue
        head_tree = parse_text(ctx.head_text(item.path))
        if head_tree is None:
            continue
        base_tree = parse_text(ctx.base_text(item.base_path))
        base_scores = function_complexity(base_tree) if base_tree is not None else {}
        head_scores = function_complexity(head_tree)

        for name, score in sorted(head_scores.items()):
            previous = base_scores.get(name)
            over_max = ctx.config.max_complexity > 0 and score > ctx.config.max_complexity
            over_delta = (
                previous is not None
                and ctx.config.complexity_delta > 0
                and score - previous > ctx.config.complexity_delta
                and score > previous
            )
            if previous is None:
                if over_max:
                    findings.append(
                        Finding(
                            "complexity",
                            "error",
                            f"new function `{name}` has complexity {score} "
                            f"(max {ctx.config.max_complexity})",
                            path=item.path,
                        )
                    )
            elif over_max or over_delta:
                findings.append(
                    Finding(
                        "complexity",
                        "error",
                        f"`{name}` complexity increased {previous} -> {score} "
                        f"(max {ctx.config.max_complexity}, delta {ctx.config.complexity_delta})",
                        path=item.path,
                    )
                )
    return findings


def check_public_api(ctx: Context) -> list[Finding]:
    valid_adrs = [item for item in _adr_changes(ctx) if _adr_valid(ctx, item)[0]]
    findings: list[Finding] = []
    for item in _relevant(ctx):
        if not item.path.endswith(".py") or not ctx.config.is_api(item.path):
            continue
        if item.status == "A":
            continue
        base_tree = parse_text(ctx.base_text(item.base_path))
        if base_tree is None:
            continue
        head_tree = parse_text(ctx.head_text(item.path)) if not item.is_deleted else None
        base_surface = public_api(base_tree)
        head_surface = public_api(head_tree) if head_tree is not None else {}

        removed = sorted(set(base_surface) - set(head_surface))
        changed = sorted(
            name for name in set(base_surface) & set(head_surface) if base_surface[name] != head_surface[name]
        )
        if not removed and not changed:
            continue

        evidence_parts: list[str] = []
        if removed:
            evidence_parts.append("removed: " + ", ".join(removed[:6]))
        if changed:
            details = "; ".join(f"{name}: {base_surface[name]} -> {head_surface[name]}" for name in changed[:4])
            evidence_parts.append("signature changes: " + details)
        message = f"breaking public API change in {item.path}"

        if not ctx.config.block_breaking_api:
            severity = "info"
        elif valid_adrs:
            severity = "warn"
            message += f" (justified by {valid_adrs[0].path})"
        else:
            severity = "error"
            message += " without an ADR"
        findings.append(
            Finding(
                "public-api",
                severity,
                message,
                path=item.path,
                evidence=" | ".join(evidence_parts)[:600],
            )
        )
    return findings


def check_new_dependencies(ctx: Context) -> list[Finding]:
    base_imports = project_imports(ctx.base_dir, ctx.config)
    head_imports = project_imports(ctx.repo, ctx.config)
    new_modules = sorted(set(head_imports) - set(base_imports))
    if not new_modules:
        return []

    stdlib = set(sys.stdlib_module_names)
    local = local_packages(ctx.repo, ctx.config)
    allowed = set(ctx.config.allow_new_dependencies)

    findings: list[Finding] = []
    for module in new_modules:
        if module in stdlib or module in local or module in allowed:
            continue
        path, line = head_imports[module]
        findings.append(
            Finding(
                "new-dependency",
                "error",
                f"new third-party dependency `{module}`",
                path=path,
                line=line,
                evidence="Add it to [aed] allow_new_dependencies and record the reason, or use an existing dependency.",
            )
        )
    return findings


def check_adr_quality(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for item in _adr_changes(ctx, include_deleted=True):
        if item.is_deleted:
            findings.append(
                Finding(
                    "adr-quality",
                    "warn",
                    "ADR deleted; supersede it instead of removing the decision history",
                    path=item.path,
                )
            )
            continue
        text = ctx.head_text(item.path) or ""
        status = _adr_status(text)
        if not any(line.startswith("# ") for line in text.splitlines()):
            findings.append(Finding("adr-quality", "warn", "ADR has no `# ` title", path=item.path))
        if not status:
            findings.append(Finding("adr-quality", "warn", "ADR has no `- Status:` line", path=item.path))
        elif status not in {"proposed", "accepted", "rejected", "superseded"}:
            findings.append(Finding("adr-quality", "warn", f"ADR has unknown status `{status}`", path=item.path))
        if re.search(r"(?mi)^##\s+Decision\b", text) is None:
            findings.append(Finding("adr-quality", "warn", "ADR has no `## Decision` section", path=item.path))
    return findings


def run_checks(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for check in (
        check_change_amplification,
        check_one_way_doors,
        check_test_tampering,
        check_import_graph,
        check_complexity,
        check_public_api,
        check_new_dependencies,
        check_adr_quality,
    ):
        findings.extend(check(ctx))
    findings.sort(key=lambda item: (-SEVERITY_RANK[item.severity], item.path, item.line or 0, item.rule))
    return findings
