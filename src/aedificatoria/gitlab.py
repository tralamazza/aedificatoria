"""Opt-in GitLab API recovery for history audits, through the GitLab CLI.

This module is only used by `aed history --gitlab-mr`; the deterministic guard
never imports or calls it. Network access, credentials, and retention are the
user's explicit choice.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Generator
from urllib.parse import quote, urlparse

from .forge import ForgeCommit, ForgeError, extract_tarball
from .git import remote_url


def parse_project_path(url: str) -> str:
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
    return path


def project_path_from_remote(repo: Path) -> str:
    return parse_project_path(remote_url(repo))


class GitLabClient:
    def __init__(
        self,
        project: str,
        cwd: Path | None = None,
        glab: str = "glab",
        timeout: float = 120.0,
    ) -> None:
        self.project = project
        self.cwd = cwd
        self.glab = glab
        self.timeout = timeout

    @property
    def encoded(self) -> str:
        return quote(self.project, safe="")

    def _run(self, path: str, paginate: bool = False) -> bytes:
        args = [self.glab, "api"]
        if paginate:
            args.append("--paginate")
        args.append(path.lstrip("/"))
        try:
            proc = subprocess.run(
                args,
                cwd=str(self.cwd) if self.cwd is not None else None,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ForgeError(
                "`glab` CLI is required for --gitlab-mr; install it and run `glab auth login`"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ForgeError(f"glab api timed out: {path}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()[:400]
            raise ForgeError(detail or f"glab api failed: {path}")
        return proc.stdout

    def _json(self, path: str, paginate: bool = False) -> Any:
        try:
            payload = json.loads(self._run(path, paginate=paginate))
        except json.JSONDecodeError as exc:
            raise ForgeError(f"glab api returned invalid JSON: {path}") from exc
        if paginate and isinstance(payload, list) and all(isinstance(page, list) for page in payload):
            items: list[Any] = []
            for page in payload:
                items.extend(page)
            return items
        return payload

    def _commit_stats(self, sha: str) -> tuple[int, int]:
        payload = self._json(f"projects/{self.encoded}/repository/commits/{sha}")
        stats = payload.get("stats") if isinstance(payload, dict) else None
        additions = stats.get("additions", 0) if isinstance(stats, dict) else 0
        deletions = stats.get("deletions", 0) if isinstance(stats, dict) else 0
        diffs = self._json(
            f"projects/{self.encoded}/repository/commits/{sha}/diff?per_page=100",
            paginate=True,
        )
        files = len(diffs) if isinstance(diffs, list) else 0
        lines = additions if isinstance(additions, int) else 0
        lines += deletions if isinstance(deletions, int) else 0
        return files, lines

    def _parse_commit(self, item: object) -> ForgeCommit | None:
        if not isinstance(item, dict):
            return None
        sha = item.get("id")
        if not isinstance(sha, str):
            return None
        title = item.get("title")
        message = item.get("message")
        subject = title if isinstance(title, str) and title else ""
        if not subject and isinstance(message, str):
            subject = message.splitlines()[0] if message else ""
        date = item.get("authored_date") or item.get("created_at") or item.get("committed_date") or ""
        parents = tuple(parent for parent in item.get("parent_ids") or [] if isinstance(parent, str))
        return ForgeCommit(sha, date if isinstance(date, str) else "", subject, parents)

    def pr_commits(self, mr: int, limit: int) -> list[ForgeCommit]:
        payload = self._json(
            f"projects/{self.encoded}/merge_requests/{mr}/commits?per_page=100",
            paginate=True,
        )
        if not isinstance(payload, list):
            raise ForgeError(f"unexpected MR commit payload for !{mr}")
        commits: list[ForgeCommit] = []
        for item in payload[-max(1, limit) :]:
            commit = self._parse_commit(item)
            if commit is None:
                continue
            files, lines = self._commit_stats(commit.sha)
            commits.append(replace(commit, files=files, lines=lines))
        return commits

    @contextmanager
    def tarball(self, sha: str) -> Generator[Path, None, None]:
        root = Path(tempfile.mkdtemp(prefix="aed-glab-"))
        try:
            archive = root / "tree.tar.gz"
            data = self._run(
                f"projects/{self.encoded}/repository/archive.tar.gz?sha={sha}"
            )
            if not data.startswith(b"\x1f\x8b"):
                raise ForgeError(f"GitLab archive for {sha[:12]} was not gzip data")
            archive.write_bytes(data)
            yield extract_tarball(archive, root / "tree")
        finally:
            shutil.rmtree(root, ignore_errors=True)
