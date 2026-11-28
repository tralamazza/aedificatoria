"""Human and JSON report rendering."""

from __future__ import annotations

import re
from typing import Any

from .model import SEVERITY_RANK, ChangedFile, Finding

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_ANNOTATION_LIMIT = 10

REMEDIES = {
    "one-way-door": "Add an accepted ADR, or revert the protected change.",
    "guard-config": "Add an accepted ADR for the policy or baseline change.",
    "change-amplification": "Split the change, or add an accepted ADR.",
    "baseline": "Fix the regression, or refresh the baseline with an accepted ADR.",
    "test-tampering": "Restore the removed test or assertion.",
    "syntax-error": "Fix the parse error.",
    "test-config": "Restore the test configuration.",
    "import-cycle": "Remove the new package cycle.",
    "forbidden-edge": "Remove the forbidden dependency edge.",
    "complexity": "Refactor below the configured threshold.",
    "public-api": "Restore compatibility, or add an accepted ADR.",
    "abi": "Restore the layout, or add an accepted ADR.",
    "new-dependency": "Reuse an existing dependency, or allowlist it with an ADR.",
    "adr-quality": "Add a title, status, and a `## Decision` section.",
    "cargo-advisory": "Review the advisory; cargo findings are informational.",
}


def safe_text(value: str) -> str:
    return _CONTROL.sub(lambda match: f"\\x{ord(match.group(0)):02x}", value)


def verdict(findings: list[Finding]) -> str:
    if any(item.severity == "error" for item in findings):
        return "block"
    if any(item.severity == "warn" for item in findings):
        return "review"
    return "allow"


def exit_code(findings: list[Finding], *, strict: bool = False) -> int:
    result = verdict(findings)
    if result == "block":
        return 2
    if result == "review" and strict:
        return 1
    return 0


def counts(findings: list[Finding]) -> dict[str, int]:
    return {
        "error": sum(1 for item in findings if item.severity == "error"),
        "warn": sum(1 for item in findings if item.severity == "warn"),
        "info": sum(1 for item in findings if item.severity == "info"),
    }


def json_payload(findings: list[Finding], base_commit: str, changed: list[ChangedFile]) -> dict[str, Any]:
    return {
        "tool": "aedificatoria",
        "verdict": verdict(findings),
        "base": base_commit,
        "changed": {
            "files": len(changed),
            "added": sum(item.added for item in changed),
            "deleted": sum(item.deleted for item in changed),
        },
        "counts": counts(findings),
        "findings": [item.as_dict() for item in findings],
    }


def render_text(findings: list[Finding], base_commit: str, changed: list[ChangedFile]) -> str:
    added = sum(item.added for item in changed)
    deleted = sum(item.deleted for item in changed)
    lines = [
        f"AEDIFICATORIA  base {base_commit[:12]}  changed {len(changed)} files (+{added}/-{deleted})",
        "",
    ]
    if not findings:
        lines.append("VERDICT: ALLOW (clean)")
        return "\n".join(lines)

    for item in findings:
        location = safe_text(item.path) if item.path else "repository"
        if item.line is not None:
            location += f":{item.line}"
        lines.append(f"[{item.severity.upper()}] {item.rule}  {location}")
        lines.append(f"        {safe_text(item.message)}")
        if item.evidence:
            lines.append(f"        evidence: {safe_text(item.evidence)}")
    summary = counts(findings)
    lines.append("")
    lines.append(
        f"VERDICT: {verdict(findings).upper()} "
        f"({summary['error']} errors, {summary['warn']} warnings, {summary['info']} info)"
    )
    return "\n".join(lines)


def _annotation_escape(value: str, *, property_value: bool = False) -> str:
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def _annotation(item: Finding) -> str:
    kind = {"error": "error", "warn": "warning", "info": "notice"}[item.severity]
    properties = [f"title={_annotation_escape(f'aed: {item.rule}', property_value=True)}"]
    if item.path:
        properties.append(f"file={_annotation_escape(item.path, property_value=True)}")
    if item.line is not None:
        properties.append(f"line={item.line}")
    message = _annotation_escape(safe_text(item.message))
    return f"::{kind} {','.join(properties)}::{message}"


def render_annotations(findings: list[Finding]) -> str:
    """Render findings as GitHub Actions workflow commands.

    Errors and warnings share the documented 10-annotation budget; notices get
    their own budget so informational findings cannot crowd out failures.
    """
    ordered = sorted(findings, key=lambda item: -SEVERITY_RANK.get(item.severity, 0))
    severe = [item for item in ordered if item.severity in {"error", "warn"}][:_ANNOTATION_LIMIT]
    notices = [item for item in ordered if item.severity == "info"][:_ANNOTATION_LIMIT]
    chosen = severe + notices
    lines = [_annotation(item) for item in chosen]
    omitted = len(findings) - len(chosen)
    if omitted > 0:
        lines.append(
            f"::notice title=aedificatoria::{omitted} additional finding(s) omitted; "
            "see the job summary"
        )
    return "\n".join(lines)


def _summary_cell(value: str) -> str:
    return safe_text(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def render_summary(findings: list[Finding], base_commit: str, changed: list[ChangedFile]) -> str:
    """Render a Markdown step summary suitable for $GITHUB_STEP_SUMMARY."""
    totals = counts(findings)
    added = sum(item.added for item in changed)
    deleted = sum(item.deleted for item in changed)
    lines = [
        f"## Aedificatoria: {verdict(findings).upper()}",
        "",
        (
            f"Base `{base_commit[:12]}` — {len(changed)} files (+{added}/-{deleted}); "
            f"{totals['error']} errors, {totals['warn']} warnings, {totals['info']} info"
        ),
        "",
    ]
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines) + "\n"

    lines.append("| Severity | Rule | Location | Finding | Remedy |")
    lines.append("|---|---|---|---|---|")
    for item in findings:
        location = _summary_cell(item.path or "repository")
        if item.line is not None:
            location += f":{item.line}"
        finding = _summary_cell(item.message)
        if item.evidence:
            finding += f"<br>{_summary_cell(item.evidence)}"
        if item.rule == "baseline" and item.severity == "info":
            remedy_text = "Run `aed baseline` to start the ratchet."
        else:
            remedy_text = REMEDIES.get(item.rule, "")
        remedy = _summary_cell(remedy_text)
        lines.append(
            f"| {item.severity.upper()} | `{item.rule}` | {location} | {finding} | {remedy} |"
        )
    return "\n".join(lines) + "\n"
