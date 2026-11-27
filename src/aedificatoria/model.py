"""Core data types shared by the checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str
    path: str = ""
    line: int | None = None
    evidence: str = ""

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path:
            payload["path"] = self.path
        if self.line is not None:
            payload["line"] = self.line
        if self.evidence:
            payload["evidence"] = self.evidence
        return payload


@dataclass(frozen=True)
class ImportRef:
    module: str
    level: int
    line: int


@dataclass(frozen=True)
class TestInfo:
    functions: frozenset[str]
    assertions: int
    skips: frozenset[str]
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: str
    old_path: str | None = None
    added: int = 0
    deleted: int = 0
    binary: bool = False

    @property
    def is_deleted(self) -> bool:
        return self.status == "D"

    @property
    def is_renamed(self) -> bool:
        return self.status in {"R", "C"}

    @property
    def base_path(self) -> str:
        return self.old_path or self.path

    @property
    def churn(self) -> int:
        return self.added + self.deleted


@dataclass
class Context:
    repo: Path
    base_ref: str
    base_commit: str
    base_dir: Path
    changed: list[ChangedFile]
    config: "Config"
    config_path: str = "aed.toml"

    def _safe_join(self, root: Path, path: str) -> Path | None:
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root.resolve()):
            return None
        return candidate

    def base_text(self, path: str) -> str | None:
        candidate = self._safe_join(self.base_dir, path)
        if candidate is None:
            return None
        try:
            return candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            return None

    def head_text(self, path: str) -> str | None:
        candidate = self._safe_join(self.repo, path)
        if candidate is None:
            return None
        try:
            return candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            return None

    def head_exists(self, path: str) -> bool:
        candidate = self._safe_join(self.repo, path)
        return bool(candidate and candidate.is_file())
