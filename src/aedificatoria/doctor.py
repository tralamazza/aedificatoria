"""Check required and optional tools used by Aedificatoria."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

_REQUIRED_PACKAGES = (
    ("tree-sitter", "tree_sitter"),
    ("tree-sitter-c", "tree_sitter_c"),
    ("tree-sitter-cpp", "tree_sitter_cpp"),
    ("tree-sitter-javascript", "tree_sitter_javascript"),
    ("tree-sitter-rust", "tree_sitter_rust"),
    ("tree-sitter-typescript", "tree_sitter_typescript"),
)
_OPTIONAL_BINARIES = (
    ("cargo", "cargo-advisory checks"),
    ("gh", "GitHub PR history audits"),
    ("glab", "GitLab MR history audits"),
)
_MIN_PYTHON = (3, 11)


@dataclass(frozen=True)
class ToolStatus:
    name: str
    kind: str
    required: bool
    installed: bool
    version: str
    detail: str
    ok: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _binary_version(name: str) -> str:
    try:
        proc = subprocess.run(
            [name, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = (proc.stdout or proc.stderr).strip()
    first = output.splitlines()[0].strip() if output else ""
    for prefix in (f"{name} version ", f"{name} "):
        if first.startswith(prefix):
            return first[len(prefix):]
    return first


def _binary(name: str, *, required: bool, detail: str = "") -> ToolStatus:
    installed = shutil.which(name) is not None
    version = _binary_version(name) if installed else ""
    if not installed:
        return ToolStatus(name, "binary", required, False, "", detail, False if required else True)
    return ToolStatus(name, "binary", required, True, version, detail, True)


def _package(name: str, module: str) -> ToolStatus:
    try:
        installed = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        installed = False
    version = ""
    if installed:
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = ""
    detail = "" if installed else "install the package or reinstall aedificatoria"
    return ToolStatus(name, "package", True, installed, version, detail, installed)


def _python() -> ToolStatus:
    version = platform.python_version()
    supported = sys.version_info >= _MIN_PYTHON
    detail = "" if supported else "requires Python 3.11 or newer"
    return ToolStatus("python", "runtime", True, True, version, detail, supported)


def collect() -> list[ToolStatus]:
    tools = [_python(), _binary("git", required=True)]
    tools.extend(_package(name, module) for name, module in _REQUIRED_PACKAGES)
    tools.extend(_binary(name, required=False, detail=purpose) for name, purpose in _OPTIONAL_BINARIES)
    return tools


def ok(tools: list[ToolStatus]) -> bool:
    return all(tool.ok for tool in tools if tool.required)


def missing_required(tools: list[ToolStatus]) -> list[str]:
    return [tool.name for tool in tools if tool.required and not tool.ok]


def missing_optional(tools: list[ToolStatus]) -> list[str]:
    return [tool.name for tool in tools if not tool.required and not tool.installed]


def payload(tools: list[ToolStatus]) -> dict[str, object]:
    return {
        "ok": ok(tools),
        "required_missing": missing_required(tools),
        "optional_missing": missing_optional(tools),
        "tools": [tool.as_dict() for tool in tools],
    }


def render_text(tools: list[ToolStatus]) -> str:
    lines = ["aed doctor"]
    for tool in tools:
        if not tool.installed:
            state = "missing"
        elif tool.ok:
            state = "ok"
        else:
            state = "incompatible"
        version = f" {tool.version}" if tool.version else ""
        scope = "required" if tool.required else "optional"
        detail = f"; {tool.detail}" if tool.detail else ""
        lines.append(f"[{state}] {tool.name}{version} ({scope}{detail})")
    required = missing_required(tools)
    optional = missing_optional(tools)
    lines.append("")
    lines.append(f"required: {'missing ' + ', '.join(required) if required else 'ok'}")
    lines.append(f"optional: {'missing ' + ', '.join(optional) if optional else 'ok'}")
    return "\n".join(lines)
