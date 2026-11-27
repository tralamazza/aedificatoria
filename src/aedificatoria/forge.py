"""Shared opt-in forge recovery primitives.

Used only by the explicit GitHub/GitLab history modes; the deterministic guard
never imports or calls this module.
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from pathlib import Path


class ForgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ForgeCommit:
    sha: str
    date: str
    subject: str
    parents: tuple[str, ...]
    files: int = 0
    lines: int = 0


def _safe_members(members: list[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    safe: list[tarfile.TarInfo] = []
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ForgeError(f"unsafe path in tarball: {member.name}")
        if member.isfile() or member.isdir():
            safe.append(member)
    return safe


def extract_tarball(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = _safe_members(tar.getmembers())
        if hasattr(tarfile, "data_filter"):
            tar.extractall(destination, members=members, filter="data")
        else:  # pragma: no cover - Python 3.11.0-3.11.3 without the backport
            tar.extractall(destination, members=members)
    entries = list(destination.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return destination
