"""Human and JSON report rendering."""

from __future__ import annotations

from typing import Any

from .model import ChangedFile, Finding


def verdict(findings: list[Finding]) -> str:
    if any(item.severity == "error" for item in findings):
        return "block"
    if any(item.severity == "warn" for item in findings):
        return "review"
    return "allow"


def exit_code(findings: list[Finding]) -> int:
    return 2 if verdict(findings) == "block" else 0


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
        location = item.path or "repository"
        if item.line is not None:
            location += f":{item.line}"
        lines.append(f"[{item.severity.upper()}] {item.rule}  {location}")
        lines.append(f"        {item.message}")
        if item.evidence:
            lines.append(f"        evidence: {item.evidence}")
    summary = counts(findings)
    lines.append("")
    lines.append(
        f"VERDICT: {verdict(findings).upper()} "
        f"({summary['error']} errors, {summary['warn']} warnings, {summary['info']} info)"
    )
    return "\n".join(lines)
