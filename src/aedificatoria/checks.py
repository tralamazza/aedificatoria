"""Deterministic architecture checks over a working-tree diff."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict

from . import cargo, languages
from .config import Config, load_config
from .model import SEVERITY_RANK, ChangedFile, Context, Finding, TestInfo
from .pyast import cycle_evidence, strongly_connected


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


def _test_config_finding(ctx: Context, item: ChangedFile) -> Finding | None:
    if item.path == "pyproject.toml":
        base_text = ctx.base_text(item.base_path) or ""
        head_text = ctx.head_text(item.path) or ""
        if "[tool.pytest" in base_text or "[tool.pytest" in head_text:
            return Finding(
                "test-config",
                "warn",
                "pytest configuration in pyproject.toml changed; verify it does not weaken the suite",
                path=item.path,
            )
        return None
    if not ctx.config.is_test_config(item.path):
        return None
    if item.is_deleted:
        return Finding(
            "test-config",
            "error",
            "test configuration deleted",
            path=item.path,
            evidence="Keep the verifier immutable; do not delete test configuration.",
        )
    if item.status == "A":
        return None
    return Finding(
        "test-config",
        "warn",
        "test configuration changed; verify it does not weaken the suite",
        path=item.path,
    )


def _deleted_test_findings(
    ctx: Context, item: ChangedFile, base_info: TestInfo, path_test: bool
) -> list[Finding]:
    if not ctx.config.block_removed_tests:
        return []
    if path_test:
        return [
            Finding(
                "test-tampering",
                "error",
                "test file deleted",
                path=item.path,
                evidence="A deleted test removes the verifier; restore it or record an ADR.",
            )
        ]
    if not base_info.functions:
        return []
    removed = sorted(base_info.functions)
    return [
        Finding(
            "test-tampering",
            "error",
            f"file with test(s) deleted: {', '.join(removed[:5])}",
            path=item.path,
            evidence="A deleted test removes the verifier; restore it or record an ADR.",
        )
    ]


def _weakened_test_findings(
    ctx: Context, item: ChangedFile, base_info: TestInfo, head_info: TestInfo
) -> list[Finding]:
    findings: list[Finding] = []
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

    per_test: list[Finding] = []
    if ctx.config.block_removed_asserts:
        for name in sorted(base_info.functions & head_info.functions):
            before = base_info.counts.get(name, 0)
            after = head_info.counts.get(name, 0)
            if before > after:
                per_test.append(
                    Finding(
                        "test-tampering",
                        "error",
                        f"test `{name}` assertions decreased from {before} to {after}",
                        path=item.path,
                        evidence="Assertions cannot be moved to other tests to hide a weakened test.",
                    )
                )
    findings.extend(per_test)

    dropped = base_info.assertions - head_info.assertions
    if dropped > 0 and ctx.config.block_removed_asserts and not per_test:
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


_GUARD_CONFIG_IGNORED = frozenset({"cargo_advisory"})


def _config_policy(config: Config) -> dict[str, object]:
    data = asdict(config)
    return {name: value for name, value in data.items() if name not in _GUARD_CONFIG_IGNORED}


def check_guard_config(ctx: Context) -> list[Finding]:
    base_policy = _config_policy(load_config(ctx.base_dir, ctx.config_path))
    head_policy = _config_policy(load_config(ctx.repo, ctx.config_path))
    changed = sorted(name for name in base_policy if base_policy[name] != head_policy.get(name))
    if not changed:
        return []

    detail = ", ".join(changed[:8])
    if len(changed) > 8:
        detail += f", +{len(changed) - 8} more"
    valid_adrs = [item for item in _adr_changes(ctx) if _adr_valid(ctx, item)[0]]
    evidence = (
        "The verifier configuration is part of the guard: thresholds, allowlists, "
        "excludes, test/API paths, and one-way doors require an accepted ADR."
    )
    if valid_adrs:
        return [
            Finding(
                "guard-config",
                "warn",
                f"verifier configuration changed ({detail}); justified by {valid_adrs[0].path}",
                path=ctx.config_path,
                evidence=evidence,
            )
        ]
    return [
        Finding(
            "guard-config",
            "error",
            f"verifier configuration changed ({detail}) without an ADR",
            path=ctx.config_path,
            evidence=evidence,
        )
    ]


def check_test_tampering(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for item in _relevant(ctx):
        config_finding = _test_config_finding(ctx, item)
        if config_finding is not None:
            findings.append(config_finding)
            continue
        if item.path == "pyproject.toml" or ctx.config.is_test_config(item.path):
            continue
        adapter = languages.get(item.path) or languages.get(item.base_path)
        if adapter is None:
            continue
        base_adapter = languages.get(item.base_path) or adapter
        base_info = base_adapter.test_info(
            base_adapter.parse_text(item.base_path, ctx.base_text(item.base_path))
        )
        head_info = adapter.test_info(adapter.parse_text(item.path, ctx.head_text(item.path)))
        path_test = ctx.config.is_test(item.path) or ctx.config.is_test(item.base_path)
        if not (path_test or base_info.functions or head_info.functions):
            continue
        if item.is_deleted:
            findings.extend(_deleted_test_findings(ctx, item, base_info, path_test))
            continue
        if item.status == "A":
            continue
        findings.extend(_weakened_test_findings(ctx, item, base_info, head_info))
    return findings


def check_import_graph(ctx: Context) -> list[Finding]:
    base_edges, base_packages = languages.package_graph(ctx.base_dir, ctx.config)
    head_edges, head_packages = languages.package_graph(ctx.repo, ctx.config)
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


def _complexity_findings(
    config: Config, path: str, base_scores: dict[str, int], head_scores: dict[str, int]
) -> list[Finding]:
    findings: list[Finding] = []
    for name, score in sorted(head_scores.items()):
        previous = base_scores.get(name)
        over_max = config.max_complexity > 0 and score > config.max_complexity
        over_delta = (
            previous is not None
            and config.complexity_delta > 0
            and score - previous > config.complexity_delta
            and score > previous
        )
        if previous is None:
            if over_max:
                findings.append(
                    Finding(
                        "complexity",
                        "error",
                        f"new function `{name}` has complexity {score} (max {config.max_complexity})",
                        path=path,
                    )
                )
        elif over_max or over_delta:
            findings.append(
                Finding(
                    "complexity",
                    "error",
                    f"`{name}` complexity increased {previous} -> {score} "
                    f"(max {config.max_complexity}, delta {config.complexity_delta})",
                    path=path,
                )
            )
    return findings


def check_complexity(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for item in _relevant(ctx):
        adapter = languages.get(item.path)
        if adapter is None or ctx.config.is_test(item.path):
            continue
        head_tree = adapter.parse_text(item.path, ctx.head_text(item.path))
        if head_tree is None:
            continue
        base_tree = (
            adapter.parse_text(item.base_path, ctx.base_text(item.base_path))
            if languages.same_language(item.base_path, item.path)
            else None
        )
        base_scores = adapter.complexity(base_tree) if base_tree is not None else {}
        findings.extend(_complexity_findings(ctx.config, item.path, base_scores, adapter.complexity(head_tree)))
    return findings


def _api_surface_change(
    base_surface: dict[str, str], head_surface: dict[str, str]
) -> tuple[list[str], list[str]]:
    removed = sorted(set(base_surface) - set(head_surface))
    changed = sorted(
        name for name in set(base_surface) & set(head_surface) if base_surface[name] != head_surface[name]
    )
    return removed, changed


def _api_evidence(
    base_surface: dict[str, str],
    head_surface: dict[str, str],
    removed: list[str],
    changed: list[str],
) -> str:
    parts: list[str] = []
    if removed:
        parts.append("removed: " + ", ".join(removed[:6]))
    if changed:
        details = "; ".join(f"{name}: {base_surface[name]} -> {head_surface[name]}" for name in changed[:4])
        parts.append("signature changes: " + details)
    return " | ".join(parts)[:600]


def _api_severity(ctx: Context, valid_adrs: list[ChangedFile], path: str) -> tuple[str, str]:
    message = f"breaking public API change in {path}"
    if not ctx.config.block_breaking_api:
        return "info", message
    if valid_adrs:
        return "warn", f"{message} (justified by {valid_adrs[0].path})"
    return "error", f"{message} without an ADR"


def check_public_api(ctx: Context) -> list[Finding]:
    valid_adrs = [item for item in _adr_changes(ctx) if _adr_valid(ctx, item)[0]]
    findings: list[Finding] = []
    for item in _relevant(ctx):
        adapter = languages.get(item.path)
        if adapter is None or not ctx.config.is_api(item.path):
            continue
        if item.status == "A" or not languages.same_language(item.base_path, item.path):
            continue
        base_tree = adapter.parse_text(item.base_path, ctx.base_text(item.base_path))
        if base_tree is None:
            continue
        head_tree = (
            adapter.parse_text(item.path, ctx.head_text(item.path)) if not item.is_deleted else None
        )
        base_surface = adapter.public_api(base_tree)
        head_surface = adapter.public_api(head_tree) if head_tree is not None else {}

        removed, changed = _api_surface_change(base_surface, head_surface)
        if not removed and not changed:
            continue
        severity, message = _api_severity(ctx, valid_adrs, item.path)
        findings.append(
            Finding(
                "public-api",
                severity,
                message,
                path=item.path,
                evidence=_api_evidence(base_surface, head_surface, removed, changed),
            )
        )
    return findings


def check_new_dependencies(ctx: Context) -> list[Finding]:
    base_imports = languages.project_imports(ctx.base_dir, ctx.config)
    head_imports = languages.project_imports(ctx.repo, ctx.config)
    new_modules = sorted(set(head_imports) - set(base_imports))
    if not new_modules:
        return []

    allowed = set(ctx.config.allow_new_dependencies)
    local: dict[str, set[str]] = {}

    findings: list[Finding] = []
    for module in new_modules:
        path, line, language = head_imports[module]
        if module in allowed:
            continue
        if language not in local:
            local[language] = languages.local_packages(ctx.repo, ctx.config, language)
        if module in local[language]:
            continue
        if languages.is_stdlib(language, module):
            continue
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


def _cargo_cycle_findings(
    base: cargo.CargoFacts | None, head: cargo.CargoFacts
) -> list[Finding]:
    base_cycles: set[tuple[str, ...]] = set()
    if base is not None:
        base_cycles = set(strongly_connected(set(base.edges), set(base.crates)))
    findings: list[Finding] = []
    for component in sorted(set(strongly_connected(set(head.edges), set(head.crates))) - base_cycles):
        findings.append(
            Finding(
                "cargo-advisory",
                "info",
                f"cargo-declared crate cycle: {', '.join(component)}",
                evidence=cycle_evidence(component, set(head.edges)),
            )
        )
    return findings


def _cargo_dependency_findings(
    ctx: Context, base: cargo.CargoFacts | None, head: cargo.CargoFacts
) -> list[Finding]:
    if base is None and (ctx.base_dir / "Cargo.toml").is_file():
        return []
    base_deps = set(base.external) if base is not None else set()
    base_scan = set(languages.declared_dependencies(ctx.base_dir, ctx.config, "rust"))
    head_scan = set(languages.declared_dependencies(ctx.repo, ctx.config, "rust"))
    cargo_new = set(head.external) - base_deps
    scan_new = (head_scan - base_scan) - set(head.crates)
    findings: list[Finding] = []
    for dependency in sorted(cargo_new - scan_new):
        findings.append(
            Finding(
                "cargo-advisory",
                "info",
                f"cargo metadata: new declared dependency `{dependency}` not seen by the manifest scan",
                evidence="Advisory only; cargo is optional and does not affect the verdict.",
            )
        )
    for dependency in sorted(scan_new - cargo_new):
        findings.append(
            Finding(
                "cargo-advisory",
                "info",
                f"manifest scan reports new dependency `{dependency}` that cargo does not attach to a package",
                evidence="Likely a workspace template or a manifest form the scan misread; advisory only.",
            )
        )
    return findings


def check_cargo_advisory(ctx: Context) -> list[Finding]:
    if not ctx.config.cargo_advisory or not cargo.available():
        return []
    head = cargo.metadata(ctx.repo)
    if head is None:
        return []
    base = cargo.metadata(ctx.base_dir)
    findings = _cargo_cycle_findings(base, head)
    findings.extend(_cargo_dependency_findings(ctx, base, head))
    return findings


def run_checks(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for check in (
        check_change_amplification,
        check_guard_config,
        check_one_way_doors,
        check_test_tampering,
        check_import_graph,
        check_complexity,
        check_public_api,
        check_new_dependencies,
        check_adr_quality,
        check_cargo_advisory,
    ):
        findings.extend(check(ctx))
    findings.sort(key=lambda item: (-SEVERITY_RANK[item.severity], item.path, item.line or 0, item.rule))
    return findings
