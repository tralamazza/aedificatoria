#!/usr/bin/env python3
"""Aggregate baseline-vs-guarded agent trial results.

This does not call a model or run tasks. An external agent harness writes one
JSONL record per task/arm using the schema in README.md; this tool computes the
comparison metrics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

def _load(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict) or not isinstance(item.get("task"), str):
            raise SystemExit(f"{path}:{number}: record must contain a task string")
        records.append(item)
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = len(records)
    totals = {
        "success": sum(bool(record.get("success")) for record in records),
        "escaped_regressions": sum(int(record.get("escaped_regressions", 0)) for record in records),
        "findings": sum(int(record.get("findings", 0)) for record in records),
        "review_minutes": sum(float(record.get("review_minutes", 0.0)) for record in records),
        "iterations": sum(int(record.get("iterations", 0)) for record in records),
    }
    means = {
        "success_rate": totals["success"] / tasks if tasks else 0.0,
        "escaped_regressions": totals["escaped_regressions"] / tasks if tasks else 0.0,
        "findings": totals["findings"] / tasks if tasks else 0.0,
        "review_minutes": totals["review_minutes"] / tasks if tasks else 0.0,
        "iterations": totals["iterations"] / tasks if tasks else 0.0,
    }
    return {"tasks": tasks, "totals": totals, "means": means}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="Baseline-arm JSONL")
    parser.add_argument("--guarded", type=Path, required=True, help="Guarded-arm JSONL")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON summary")
    args = parser.parse_args(argv)

    baseline = _load(args.baseline)
    guarded = _load(args.guarded)
    base_summary = summarize(baseline)
    guard_summary = summarize(guarded)
    delta = {
        key: round(guard_summary["means"][key] - base_summary["means"][key], 4)
        for key in base_summary["means"]
    }
    payload = {
        "protocol": "agent-ab-v1",
        "baseline": base_summary,
        "guarded": guard_summary,
        "delta": delta,
    }
    print(json.dumps(payload, indent=2))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
