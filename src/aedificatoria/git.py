"""Git plumbing: diff discovery, base worktrees, and Python file walking."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from .config import Config
from .model import ChangedFile


class GitError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or proc.stdout.decode("utf-8", "replace").strip()
        raise GitError(detail or f"git {' '.join(args)} failed")
    return proc


def repo_root(path: Path) -> Path:
    target = path.resolve()
    if target.is_file():
        target = target.parent
    proc = _git(target, "rev-parse", "--show-toplevel")
    root = os.fsdecode(proc.stdout).strip()
    if not root:
        raise GitError(f"not a git repository: {path}")
    return Path(root)


def resolve_commit(repo: Path, ref: str) -> str:
    proc = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return os.fsdecode(proc.stdout).strip()


def _numstat(repo: Path, base: str) -> dict[str, tuple[int, int, bool]]:
    proc = _git(repo, "diff", "--numstat", base, "--")
    stats: dict[str, tuple[int, int, bool]] = {}
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw, path = parts[0], parts[1], parts[2]
        if " => " in path:
            path = path.split(" => ", 1)[1].strip()
            if path.startswith("{") and path.endswith("}"):
                path = path.strip("{}")
        binary = added_raw == "-" or deleted_raw == "-"
        added = 0 if added_raw == "-" else int(added_raw)
        deleted = 0 if deleted_raw == "-" else int(deleted_raw)
        stats[path] = (added, deleted, binary)
    return stats


def _untracked(repo: Path) -> list[ChangedFile]:
    proc = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    out: list[ChangedFile] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        path = repo / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        binary = b"\0" in data[:8000]
        added = 0 if binary else data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        out.append(ChangedFile(status="A", path=rel, added=added, binary=binary))
    return out


def changed_files(repo: Path, base_commit: str) -> list[ChangedFile]:
    """Return working-tree changes against `base_commit`, including untracked files."""
    proc = _git(repo, "diff", "--name-status", "--find-renames", "-z", base_commit, "--")
    tokens = proc.stdout.split(b"\0")
    changed: list[ChangedFile] = []
    index = 0
    while index < len(tokens):
        status_raw = tokens[index]
        index += 1
        if not status_raw:
            continue
        status = os.fsdecode(status_raw)
        code = status[0]
        if code in {"R", "C"}:
            if index + 1 >= len(tokens):
                break
            old_path = os.fsdecode(tokens[index])
            new_path = os.fsdecode(tokens[index + 1])
            index += 2
            changed.append(ChangedFile(status=code, path=new_path, old_path=old_path))
        else:
            if index >= len(tokens):
                break
            path = os.fsdecode(tokens[index])
            index += 1
            changed.append(ChangedFile(status=code, path=path))

    stats = _numstat(repo, base_commit)
    enriched: list[ChangedFile] = []
    for item in changed:
        added, deleted, binary = stats.get(item.path, stats.get(item.base_path, (0, 0, False)))
        enriched.append(
            ChangedFile(
                status=item.status,
                path=item.path,
                old_path=item.old_path,
                added=added,
                deleted=deleted,
                binary=binary,
            )
        )

    tracked_paths = {item.path for item in enriched}
    enriched.extend(item for item in _untracked(repo) if item.path not in tracked_paths)
    enriched.sort(key=lambda item: item.path)
    return enriched


@contextmanager
def base_worktree(repo: Path, commit: str) -> Generator[Path, None, None]:
    """Create a detached worktree of `commit`; remove it on exit."""
    tmp_root = Path(tempfile.mkdtemp(prefix="aed-base-"))
    tree = tmp_root / "tree"
    proc = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "--force", str(tree), commit],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(detail or f"could not create base worktree at {commit}")
    try:
        yield tree
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(tmp_root, ignore_errors=True)


_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}


def tracked_python_files(root: Path, config: Config) -> list[str]:
    """List repository-relative `.py` files, excluding generated/vendor trees."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(root).as_posix()
            except ValueError:
                continue
            if config.is_excluded(rel):
                continue
            out.append(rel)
    out.sort()
    return out
