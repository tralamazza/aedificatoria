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
        tar.extractall(destination, members=_safe_members(tar.getmembers()), filter="data")
    entries = list(destination.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return destination
