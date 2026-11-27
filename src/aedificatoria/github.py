"""Opt-in GitHub API recovery for history audits, through the GitHub CLI.

This module is only used by `aed history --github-pr`; the deterministic guard
never imports or calls it. Network access, credentials, and retention are the
user's explicit choice.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlparse

from .git import remote_url


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrCommit:
    sha: str
    date: str
    subject: str
    parents: tuple[str, ...]
    files: int = 0
    lines: int = 0


def parse_slug(url: str) -> str:
    value = url.strip()
    if value.startswith("git@"):
        path = value.split(":", 1)[1]
    elif "://" in value:
        path = urlparse(value).path
    else:
        path = value
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return ""
    return f"{parts[-2]}/{parts[-1]}"


def slug_from_remote(repo: Path) -> str:
    return parse_slug(remote_url(repo))


def _safe_members(members: list[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    safe: list[tarfile.TarInfo] = []
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise GitHubError(f"unsafe path in tarball: {member.name}")
        if member.isfile() or member.isdir():
            safe.append(member)
    return safe


def extract_tarball(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(destination, members=_safe_members(tar.getmembers()), filter="data")
    entries = list(destination.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return destination


class GitHubClient:
    def __init__(self, repo: str, gh: str = "gh", timeout: float = 120.0) -> None:
        self.repo = repo
        self.gh = gh
        self.timeout = timeout

    def _run(self, path: str, paginate: bool = False) -> bytes:
        args = [self.gh, "api"]
        if paginate:
            args.extend(["--paginate", "--slurp"])
        args.append(path.lstrip("/"))
        try:
            proc = subprocess.run(args, capture_output=True, timeout=self.timeout, check=False)
        except FileNotFoundError as exc:
            raise GitHubError(
                "`gh` CLI is required for --github-pr; install it and run `gh auth login`"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh api timed out: {path}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()[:400]
            raise GitHubError(detail or f"gh api failed: {path}")
        return proc.stdout

    def _json(self, path: str, paginate: bool = False) -> Any:
        try:
            payload = json.loads(self._run(path, paginate=paginate))
        except json.JSONDecodeError as exc:
            raise GitHubError(f"gh api returned invalid JSON: {path}") from exc
        if paginate and isinstance(payload, list):
            items: list[Any] = []
            for page in payload:
                if isinstance(page, list):
                    items.extend(page)
                else:
                    items.append(page)
            return items
        return payload

    def _commit_stats(self, sha: str) -> tuple[int, int]:
        payload = self._json(f"repos/{self.repo}/commits/{sha}")
        if not isinstance(payload, dict):
            return 0, 0
        stats = payload.get("stats")
        files = payload.get("files")
        additions = stats.get("additions", 0) if isinstance(stats, dict) else 0
        deletions = stats.get("deletions", 0) if isinstance(stats, dict) else 0
        count = len(files) if isinstance(files, list) else 0
        lines = additions if isinstance(additions, int) else 0
        lines += deletions if isinstance(deletions, int) else 0
        return count, lines

    def _parse_commit(self, item: object) -> PrCommit | None:
        if not isinstance(item, dict):
            return None
        sha = item.get("sha")
        commit = item.get("commit")
        if not isinstance(sha, str) or not isinstance(commit, dict):
            return None
        message = commit.get("message")
        author = commit.get("author")
        date = author.get("date") if isinstance(author, dict) else ""
        parents = tuple(
            parent["sha"]
            for parent in item.get("parents", [])
            if isinstance(parent, dict) and isinstance(parent.get("sha"), str)
        )
        subject = message.splitlines()[0] if isinstance(message, str) and message else ""
        return PrCommit(sha, date if isinstance(date, str) else "", subject, parents)

    def pr_commits(self, pr: int, limit: int) -> list[PrCommit]:
        payload = self._json(f"repos/{self.repo}/pulls/{pr}/commits", paginate=True)
        if not isinstance(payload, list):
            raise GitHubError(f"unexpected PR commit payload for #{pr}")
        commits: list[PrCommit] = []
        for item in payload[-max(1, limit) :]:
            commit = self._parse_commit(item)
            if commit is None:
                continue
            files, lines = self._commit_stats(commit.sha)
            commits.append(replace(commit, files=files, lines=lines))
        return commits

    @contextmanager
    def tarball(self, sha: str) -> Generator[Path, None, None]:
        root = Path(tempfile.mkdtemp(prefix="aed-gh-"))
        try:
            archive = root / "tree.tar.gz"
            data = self._run(f"repos/{self.repo}/tarball/{sha}")
            if not data.startswith(b"\x1f\x8b"):
                raise GitHubError(f"GitHub tarball for {sha[:12]} was not gzip data")
            archive.write_bytes(data)
            yield extract_tarball(archive, root / "tree")
        finally:
            shutil.rmtree(root, ignore_errors=True)
