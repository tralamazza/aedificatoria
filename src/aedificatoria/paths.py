"""Filesystem confinement and bounded reads for repository-controlled paths."""

from __future__ import annotations

from pathlib import Path

MAX_SOURCE_BYTES = 8 * 1024 * 1024


class ConfinedPathError(ValueError):
    pass


def confined(root: Path, relative: str | Path) -> Path:
    """Resolve a relative path and reject escapes or symlink targets outside root."""
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ConfinedPathError(f"absolute path is not allowed: {relative}")
    if ".." in candidate.parts:
        raise ConfinedPathError(f"parent traversal is not allowed: {relative}")
    root_resolved = root.resolve()
    try:
        resolved = (root_resolved / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise ConfinedPathError(f"cannot resolve path: {relative}") from exc
    if not resolved.is_relative_to(root_resolved):
        raise ConfinedPathError(f"path escapes repository: {relative}")
    return resolved


def read_text(
    root: Path,
    relative: str | Path,
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
    errors: str = "strict",
) -> str:
    """Read a confined regular file, rejecting symlinks and oversized blobs."""
    raw = root / Path(relative)
    if raw.is_symlink():
        raise ConfinedPathError(f"symlink is not allowed: {relative}")
    resolved = confined(root, relative)
    if not resolved.is_file():
        raise FileNotFoundError(str(raw))
    if resolved.stat().st_size > max_bytes:
        raise ConfinedPathError(f"file exceeds size limit: {relative}")
    return resolved.read_text(encoding="utf-8", errors=errors)
