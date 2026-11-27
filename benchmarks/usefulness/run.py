#!/usr/bin/env python3
"""Deterministic mutation benchmark for Aedificatoria.

Each case creates a temporary Git repository, commits a clean base, applies a
change, runs `aed check --json`, and compares the verdict/rules with labels.

This measures rule usefulness, not agent usefulness. Agent A/B methodology is in
README.md; this harness provides the repeatable mutation corpus.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CaseResult:
    name: str
    expect: str
    got: str
    expected_rules: list[str]
    matched_rules: list[str]
    passed: bool
    findings: list[dict[str, Any]]
    duration_s: float
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expect": self.expect,
            "got": self.got,
            "expected_rules": self.expected_rules,
            "matched_rules": self.matched_rules,
            "passed": self.passed,
            "findings": self.findings,
            "duration_s": round(self.duration_s, 3),
            **({"error": self.error} if self.error else {}),
        }


def _run(cmd: list[str], cwd: Path, *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _apply_change(root: Path, change: dict[str, Any]) -> None:
    op = change.get("op")
    if op == "write":
        _write(root, str(change["path"]), str(change.get("content", "")))
    elif op == "delete":
        (root / str(change["path"])).unlink(missing_ok=True)
    elif op == "rename":
        source = root / str(change["from"])
        target = root / str(change["to"])
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    elif op == "write_many":
        prefix = str(change.get("prefix", "bulk/"))
        count = int(change.get("count", 0))
        content = str(change.get("content", "value = 1\n"))
        for index in range(count):
            _write(root, f"{prefix}{index:04d}.py", content)
    else:
        raise ValueError(f"unknown change op: {op!r}")


def _prepare_repo(case: dict[str, Any], root: Path) -> None:
    init = _run(["git", "init", "-q"], root)
    if init.returncode != 0:
        raise RuntimeError(init.stderr)
    for key, value in {
        "user.email": "usefulness@example.invalid",
        "user.name": "Usefulness Benchmark",
    }.items():
        proc = _run(["git", "config", key, value], root)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)

    base = dict(case.get("base", {}))
    base.setdefault("README.md", "base\n")
    for rel, content in base.items():
        _write(root, rel, str(content))
    proc = _run(["git", "add", "-A"], root)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    proc = _run(["git", "commit", "-q", "-m", "base"], root)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    for change in case.get("changes", []):
        _apply_change(root, change)


def _matched_rules(expected: list[str], findings: list[dict[str, Any]], severity: str) -> list[str]:
    present = {
        str(item.get("rule"))
        for item in findings
        if item.get("severity") == severity
    }
    return [rule for rule in expected if rule in present]


def run_case(case: dict[str, Any], *, timeout: float = 120) -> CaseResult:
    name = str(case.get("name", "unnamed"))
    expected = str(case.get("expect", "allow"))
    expected_rules = [str(rule) for rule in case.get("rules", [])]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="aed-usefulness-") as raw_root:
        root = Path(raw_root)
        try:
            _prepare_repo(case, root)
            proc = _run(
                [sys.executable, "-m", "aedificatoria", "check", "--path", str(root), "--json"],
                root,
                timeout=timeout,
            )
        except Exception as exc:  # benchmark must report failures, not abort the suite
            return CaseResult(
                name, expected, "error", expected_rules, [], False, [], time.monotonic() - started, str(exc)
            )

    duration = time.monotonic() - started
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return CaseResult(
            name,
            expected,
            "error",
            expected_rules,
            [],
            False,
            [],
            duration,
            proc.stderr.strip() or "aed produced no JSON",
        )

    verdict = str(payload.get("verdict", "error"))
    findings = payload.get("findings", [])
    findings = findings if isinstance(findings, list) else []
    if expected == "allow":
        passed = verdict == "allow"
        matched = []
    else:
        severity = "warn" if expected == "review" else "error"
        matched = _matched_rules(expected_rules, findings, severity)
        passed = verdict == expected and len(matched) == len(expected_rules)
    return CaseResult(
        name,
        expected,
        verdict,
        expected_rules,
        matched,
        passed,
        findings,
        duration,
    )


def _summarize(results: list[CaseResult]) -> dict[str, Any]:
    positives = [item for item in results if item.expect != "allow"]
    negatives = [item for item in results if item.expect == "allow"]
    tp = sum(item.passed for item in positives)
    fn = len(positives) - tp
    fp = sum(not item.passed for item in negatives)
    tn = len(negatives) - fp
    by_rule: dict[str, dict[str, int]] = {}
    for item in positives:
        for rule in item.expected_rules:
            bucket = by_rule.setdefault(rule, {"expected": 0, "detected": 0})
            bucket["expected"] += 1
            if rule in item.matched_rules:
                bucket["detected"] += 1
    review_findings = sum(
        len([finding for finding in item.findings if finding.get("severity") in {"error", "warn"}])
        for item in negatives
    )
    return {
        "cases": len(results),
        "positives": len(positives),
        "negatives": len(negatives),
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "false_block_rate": round(fp / len(negatives), 4) if negatives else None,
        "review_findings_on_clean_cases": review_findings,
        "by_rule": dict(sorted(by_rule.items())),
    }


def _print_text(results: list[CaseResult], summary: dict[str, Any]) -> None:
    for item in results:
        status = "PASS" if item.passed else "FAIL"
        rules = ",".join(item.expected_rules) or "-"
        print(f"{status:4} {item.name:32} expect={item.expect:6} got={item.got:8} rules={rules}")
    print()
    print(
        f"cases={summary['cases']} pass={summary['tp'] + summary['tn']} "
        f"tp={summary['tp']} fn={summary['fn']} fp={summary['fp']} tn={summary['tn']} "
        f"precision={summary['precision']} recall={summary['recall']} "
        f"false_block_rate={summary['false_block_rate']} "
        f"review_findings_on_clean={summary['review_findings_on_clean_cases']}"
    )
    for rule, values in summary["by_rule"].items():
        print(f"  {rule}: {values['detected']}/{values['expected']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("cases.json"),
        help="Labeled case JSON (default: cases.json beside this script)",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write JSON results")
    parser.add_argument("--timeout", type=float, default=120, help="Per-case timeout seconds")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Fail if recall < 0.9 or false-block rate > 0.05",
    )
    args = parser.parse_args(argv)

    data = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = data.get("cases", data) if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise SystemExit("case file must contain a list or a {cases: [...]} object")

    results = [run_case(case, timeout=args.timeout) for case in cases]
    summary = _summarize(results)
    payload = {
        "suite": "usefulness-mutations-v1",
        "cases": [item.as_dict() for item in results],
        "summary": summary,
    }
    _print_text(results, summary)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    if args.gate:
        recall = summary["recall"]
        false_block = summary["false_block_rate"]
        if recall is not None and recall < 0.9:
            return 1
        if false_block is not None and false_block > 0.05:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
