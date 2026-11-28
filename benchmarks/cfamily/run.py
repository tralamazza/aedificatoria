#!/usr/bin/env python3
"""Audit the C/C++ adapter against pinned real-world repositories.

This is a diagnostic corpus, not a gate: it uses network to clone external repos
unless they are already in the cache and measures history diagnostics/runtime,
not labeled precision.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: Path, *, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _ensure_repo(repo: dict[str, Any], cache: Path, *, offline: bool) -> Path:
    target = cache / str(repo["id"])
    git = target / ".git"
    if not git.exists():
        if offline:
            raise RuntimeError(f"corpus repository missing in offline mode: {target}")
        cache.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = _run(["git", "clone", "--quiet", str(repo["url"]), str(target)], cache)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"clone failed: {repo['url']}")
    commit = str(repo["commit"])
    proc = _run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], target)
    if proc.returncode != 0:
        if offline:
            raise RuntimeError(f"pinned commit missing in offline mode: {repo['id']} {commit}")
        proc = _run(["git", "fetch", "--quiet", "origin", commit], target)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"fetch failed: {repo['id']} {commit}")
    proc = _run(["git", "checkout", "--quiet", "--detach", "--force", commit], target)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"checkout failed: {repo['id']} {commit}")
    return target


def _audit(repo: dict[str, Any], root: Path, *, limit: int, jobs: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    proc = _run(
        [
            sys.executable,
            "-m",
            "aedificatoria",
            "history",
            "--path",
            str(root),
            "--limit",
            str(limit),
            "--jobs",
            str(jobs),
            "--json",
        ],
        root,
        timeout=timeout,
    )
    duration = time.monotonic() - started
    result: dict[str, Any] = {
        "id": repo["id"],
        "language": repo["language"],
        "commit": repo["commit"],
        "duration_s": round(duration, 3),
    }
    if proc.returncode != 0:
        result["error"] = proc.stderr.strip() or proc.stdout[-2000:] or f"exit {proc.returncode}"
        return result
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        result["error"] = f"invalid JSON: {exc}"
        return result
    commits = payload.get("commits", [])
    rules: Counter[str] = Counter()
    findings = 0
    for commit in commits:
        for finding in commit.get("regressions", []):
            rules[str(finding.get("rule"))] += 1
            findings += 1
    result.update(
        {
            "commits": len(commits),
            "findings": findings,
            "commits_with_findings": sum(1 for commit in commits if commit.get("regressions")),
            "worst": len(payload.get("worst", [])),
            "rules": dict(sorted(rules.items())),
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("repos.json"),
        help="Pinned repository manifest",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("/tmp/aed-cfamily-corpus"),
        help="Clone cache directory",
    )
    parser.add_argument("--limit", type=int, default=100, help="First-parent commits per repo")
    parser.add_argument("--jobs", type=int, default=4, help="History workers per repo")
    parser.add_argument("--timeout", type=float, default=600, help="Per-repo timeout")
    parser.add_argument("--offline", action="store_true", help="Never network-clone/fetch")
    parser.add_argument("--repo", action="append", default=[], help="Only audit these repo IDs")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON results")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    repos = manifest.get("repos", [])
    if args.repo:
        wanted = set(args.repo)
        repos = [repo for repo in repos if repo.get("id") in wanted]
    results: list[dict[str, Any]] = []
    for repo in repos:
        print(f"auditing {repo['id']} {repo['language']} {repo['commit'][:12]}", flush=True)
        try:
            root = _ensure_repo(repo, args.cache, offline=args.offline)
            result = _audit(
                repo,
                root,
                limit=max(1, args.limit),
                jobs=max(1, args.jobs),
                timeout=args.timeout,
            )
        except Exception as exc:  # corpus must report, not abort
            result = {
                "id": repo.get("id"),
                "language": repo.get("language"),
                "commit": repo.get("commit"),
                "error": str(exc),
            }
        results.append(result)
        if "error" in result:
            print(f"  ERROR {result['error']}", flush=True)
        else:
            print(
                f"  {result['duration_s']}s commits={result['commits']} "
                f"findings={result['findings']} rules={result['rules']}",
                flush=True,
            )

    payload = {
        "suite": "cfamily-real-world-v1",
        "limit": args.limit,
        "jobs": args.jobs,
        "repos": results,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
