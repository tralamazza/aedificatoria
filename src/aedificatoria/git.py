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


_HOOK_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")


def _clean_env(*, keep_index: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    for name in _HOOK_GIT_ENV:
        if keep_index and name == "GIT_INDEX_FILE":
            continue
        env.pop(name, None)
    return env


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    keep_index: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        env=_clean_env(keep_index=keep_index),
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


def commit_exists(repo: Path, ref: str) -> bool:
    proc = _git(repo, "cat-file", "-e", f"{ref}^{{commit}}", check=False)
    return proc.returncode == 0


def remote_url(repo: Path, name: str = "origin") -> str:
    proc = _git(repo, "remote", "get-url", name, check=False)
    return os.fsdecode(proc.stdout).strip() if proc.returncode == 0 else ""


def history_commits(
    repo: Path,
    limit: int,
    since: str | None = None,
    head: str = "HEAD",
) -> list[tuple[str, str, str]]:
    """Return up to `limit` first-parent commits ending at `head`, oldest first."""
    revision = f"{since}..{head}" if since else head
    proc = _git(
        repo,
        "log",
        "--first-parent",
        f"--max-count={max(1, limit)}",
        "--format=%H%x09%aI%x09%s",
        revision,
    )
    rows: list[tuple[str, str, str]] = []
    for line in os.fsdecode(proc.stdout).splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    rows.reverse()
    return rows


def commit_parents(repo: Path, ref: str) -> tuple[str, ...]:
    proc = _git(repo, "show", "-s", "--format=%P", ref)
    return tuple(os.fsdecode(proc.stdout).split())


def diff_stats(repo: Path, base: str | None, head: str) -> tuple[int, int]:
    """Return (files, changed lines) for head against base, or its parent."""
    if base:
        proc = _git(repo, "diff", "--numstat", base, head, "--")
    else:
        proc = _git(repo, "diff-tree", "--root", "--no-commit-id", "--numstat", "-r", head)
    files = 0
    lines = 0
    for row in os.fsdecode(proc.stdout).splitlines():
        parts = row.split("\t", 2)
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            lines += int(parts[0])
        if parts[1].isdigit():
            lines += int(parts[1])
    return files, lines


def merge_edited_files(repo: Path, commit: str) -> list[str]:
    """Return files with lines changed relative to every parent.

    A combined diff shows only files modified from all parents. Lines prefixed
    entirely with `+` or `-` are changes relative to every parent, which includes
    conflict-resolution/manual merge edits. Same-file auto-merges that only
    combine parent lines do not qualify.
    """
    parents = commit_parents(repo, commit)
    width = len(parents)
    if width < 2:
        return []
    proc = _git(repo, "show", "--cc", "--format=", commit)
    paths: set[str] = set()
    current: str | None = None
    for raw in proc.stdout.splitlines():
        line = os.fsdecode(raw)
        if line.startswith("diff --cc "):
            current = line[len("diff --cc ") :]
            continue
        if line.startswith("diff --combined "):
            current = line[len("diff --combined ") :]
            continue
        if current is None or line.startswith(("@@", "--- ", "+++ ", "index ")):
            continue
        prefix = line[:width]
        if prefix and (set(prefix) == {"+"} or set(prefix) == {"-"}):
            paths.add(current)
    return sorted(paths)


def history_graph(
    repo: Path,
    limit: int,
    since: str | None = None,
    head: str = "HEAD",
) -> list[tuple[str, tuple[str, ...], str, str]]:
    """Return up to `limit` reachable commits with parents, oldest first."""
    revision = f"{since}..{head}" if since else head
    proc = _git(
        repo,
        "log",
        "--topo-order",
        f"--max-count={max(1, limit)}",
        "--format=%H%x09%P%x09%aI%x09%s",
        revision,
    )
    rows: list[tuple[str, tuple[str, ...], str, str]] = []
    for line in os.fsdecode(proc.stdout).splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            rows.append((parts[0], tuple(parts[1].split()), parts[2], parts[3]))
    rows.reverse()
    return rows


def _numstat(repo: Path, base: str, *, staged: bool = False) -> dict[str, tuple[int, int, bool]]:
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--numstat", base, "--"])
    proc = _git(repo, *args, keep_index=staged)
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


def changed_files(repo: Path, base_commit: str, *, staged: bool = False) -> list[ChangedFile]:
    """Return changes against `base_commit`, from the index or working tree.

    The index mode excludes untracked files because they cannot enter the commit.
    """
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--name-status", "--find-renames", "-z", base_commit, "--"])
    proc = _git(repo, *args, keep_index=staged)
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

    stats = _numstat(repo, base_commit, staged=staged)
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
    if not staged:
        enriched.extend(item for item in _untracked(repo) if item.path not in tracked_paths)
    enriched.sort(key=lambda item: item.path)
    return enriched


@contextmanager
def index_tree(repo: Path) -> Generator[Path, None, None]:
    """Materialize the staged index into a temporary directory."""
    tmp_root = Path(tempfile.mkdtemp(prefix="aed-index-"))
    tree = tmp_root / "tree"
    tree.mkdir()
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "checkout-index",
            "--all",
            "--force",
            f"--prefix={tree}{os.sep}",
        ],
        capture_output=True,
        check=False,
        env=_clean_env(keep_index=True),
    )
    if proc.returncode != 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(detail or "could not materialize the staged index")
    try:
        yield tree
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@contextmanager
def base_worktree(repo: Path, commit: str) -> Generator[Path, None, None]:
    """Create a detached worktree of `commit`; remove it on exit."""
    tmp_root = Path(tempfile.mkdtemp(prefix="aed-base-"))
    tree = tmp_root / f"tree-{tmp_root.name}"
    proc = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "--force", str(tree), commit],
        capture_output=True,
        check=False,
        env=_clean_env(),
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
            env=_clean_env(),
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
    "target",
    "coverage",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".cache",
    "out",
}


def tracked_source_files(root: Path, config: Config, extensions: tuple[str, ...]) -> list[str]:
    """List repository-relative source files with the given extensions."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(extensions):
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


def tracked_python_files(root: Path, config: Config) -> list[str]:
    """List repository-relative `.py` files, excluding generated/vendor trees."""
    return tracked_source_files(root, config, (".py",))
