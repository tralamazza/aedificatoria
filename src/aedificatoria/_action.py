"""GitHub Actions plumbing: base resolution and step outputs.

Internal to the repository's composite action. This module is not part of the
stable ``aed`` CLI surface.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ZERO_SHA = "0" * 40
SHALLOW_DEPTHS = (50, 200, 1000)


class ActionError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
    except OSError as exc:
        raise ActionError(f"could not run git: {exc}") from exc


def _valid_ref(ref: str) -> bool:
    return bool(ref) and not ref.startswith("-") and not any(
        char.isspace() or char == "\0" for char in ref
    )


def _commit(repo: Path, ref: str) -> str:
    if not _valid_ref(ref):
        return ""
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _fetch_commit(repo: Path, remote: str, ref: str) -> bool:
    proc = _git(repo, "fetch", "--no-tags", "--depth=1", remote, ref)
    return proc.returncode == 0


def _fetch_branch(repo: Path, remote: str, branch: str, depth: int) -> bool:
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    proc = _git(repo, "fetch", "--no-tags", f"--depth={depth}", remote, refspec)
    return proc.returncode == 0


def _deepen_branch(repo: Path, remote: str, branch: str, depth: int) -> bool:
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    proc = _git(repo, "fetch", "--no-tags", f"--deepen={depth}", remote, refspec)
    return proc.returncode == 0


def _merge_base(repo: Path, left: str, right: str) -> str:
    proc = _git(repo, "merge-base", left, right)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def event_base(event: dict) -> str:
    """Return the base commit candidate encoded in a GitHub event payload."""
    merge_group = event.get("merge_group")
    if isinstance(merge_group, dict):
        sha = merge_group.get("base_sha")
        if isinstance(sha, str) and sha:
            return sha
    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        base = pull_request.get("base")
        if isinstance(base, dict):
            sha = base.get("sha")
            if isinstance(sha, str) and sha:
                return sha
    before = event.get("before")
    return before if isinstance(before, str) else ""


def _parent(repo: Path, remote: str) -> str:
    parent = _commit(repo, "HEAD~1")
    if parent:
        return parent
    _git(repo, "fetch", "--no-tags", "--deepen=1", remote)
    return _commit(repo, "HEAD~1")


def _resolve_fallback(repo: Path, default_branch: str, remote: str) -> str:
    if not _valid_ref(default_branch) or not _valid_ref(remote):
        raise ActionError("cannot resolve a base commit; pass the base input")
    head = _commit(repo, "HEAD")
    if not head:
        raise ActionError("HEAD is not a commit; pass the base input")
    if not _fetch_branch(repo, remote, default_branch, SHALLOW_DEPTHS[0]):
        raise ActionError(
            f"could not fetch {remote}/{default_branch}; pass the base input"
        )
    for index, deepen in enumerate((0, *SHALLOW_DEPTHS[1:])):
        if index:
            _deepen_branch(repo, remote, default_branch, deepen)
        base = _merge_base(repo, "HEAD", f"{remote}/{default_branch}")
        if base and base != head:
            return base
        if base == head:
            parent = _parent(repo, remote)
            if parent:
                return parent
            break
    raise ActionError(
        f"could not find a merge base with {remote}/{default_branch}; "
        "pass the base input or check out full history"
    )


def _resolve_commit(repo: Path, remote: str, candidate: str) -> str:
    """Resolve a local ref, or fetch it (branch names, remote refs, SHAs)."""
    sha = _commit(repo, candidate)
    if sha:
        return sha
    fetch_ref = candidate
    prefix = f"{remote}/"
    if fetch_ref.startswith(prefix):
        fetch_ref = fetch_ref[len(prefix) :]
    if not _valid_ref(fetch_ref) or not _fetch_commit(repo, remote, fetch_ref):
        return ""
    for ref in (
        candidate,
        "FETCH_HEAD",
        f"{remote}/{candidate}",
        f"{remote}/{fetch_ref}",
    ):
        sha = _commit(repo, ref)
        if sha:
            return sha
    return ""


def resolve_base(
    repo: Path,
    event: dict,
    *,
    default_branch: str,
    explicit: str = "",
    remote: str = "origin",
) -> str:
    """Resolve and, when needed, fetch the commit the diff is measured against."""
    candidate = explicit.strip()
    if candidate:
        if not _valid_ref(candidate):
            raise ActionError(f"invalid base input: {candidate!r}")
        sha = _resolve_commit(repo, remote, candidate)
        if not sha:
            raise ActionError(f"base {candidate!r} is not reachable from {remote}")
        return sha

    candidate = event_base(event)
    if candidate and candidate != ZERO_SHA:
        sha = _resolve_commit(repo, remote, candidate)
        if sha:
            return sha
    return _resolve_fallback(repo, default_branch, remote)


def _read_event(path: str) -> dict:
    if not path:
        return {}
    event_path = Path(path)
    if not event_path.is_file():
        raise ActionError(f"event payload not found: {path}")
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionError(f"could not read event payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise ActionError("event payload is not a JSON object")
    return payload


def _output_value(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def _cmd_resolve_base(args: argparse.Namespace) -> int:
    repo = Path(args.path).resolve()
    event = _read_event(args.event)
    sha = resolve_base(
        repo,
        event,
        default_branch=args.default_branch,
        explicit=args.base,
        remote=args.remote,
    )
    print(sha)
    return 0


def _cmd_outputs(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionError(f"could not read report: {exc}") from exc
    if not isinstance(payload, dict):
        raise ActionError("report is not a JSON object")
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        counts = {}
    lines = [
        ("verdict", payload.get("verdict", "")),
        ("base", payload.get("base", "")),
        ("errors", counts.get("error", 0)),
        ("warnings", counts.get("warn", 0)),
        ("infos", counts.get("info", 0)),
        ("exit-code", args.exit_code),
        ("json", Path(args.json).resolve()),
    ]
    text = "".join(f"{name}={_output_value(value)}\n" for name, value in lines)
    if args.output:
        try:
            with Path(args.output).open("a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            raise ActionError(f"could not write step outputs: {exc}") from exc
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aedificatoria._action")
    sub = parser.add_subparsers(dest="cmd", required=True)

    resolve = sub.add_parser("resolve-base", help="Resolve and fetch the diff base")
    resolve.add_argument("--path", default=".")
    resolve.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    resolve.add_argument("--base", default="")
    resolve.add_argument("--default-branch", default="")
    resolve.add_argument("--remote", default="origin")

    outputs = sub.add_parser("outputs", help="Write action outputs from a report")
    outputs.add_argument("--json", required=True)
    outputs.add_argument("--exit-code", type=int, default=0)
    outputs.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT", ""))

    args = parser.parse_args(argv)
    try:
        if args.cmd == "resolve-base":
            return _cmd_resolve_base(args)
        return _cmd_outputs(args)
    except ActionError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
