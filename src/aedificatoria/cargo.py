"""Optional cargo metadata: advisory facts that never affect the verdict.

Only `cargo metadata --no-deps --offline` is used: it reads manifests, does not
resolve dependencies, does not build, and does not write lockfiles or targets.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CargoFacts:
    crates: frozenset[str]
    external: frozenset[str]
    edges: frozenset[tuple[str, str]]


def available() -> bool:
    return shutil.which("cargo") is not None


def metadata(root: Path) -> CargoFacts | None:
    if not (root / "Cargo.toml").is_file():
        return None
    try:
        proc = subprocess.run(
            ["cargo", "metadata", "--no-deps", "--offline", "--format-version", "1"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return _facts(payload)


def _normalize(name: str) -> str:
    return name.replace("-", "_")


def _facts(payload: object) -> CargoFacts | None:
    if not isinstance(payload, dict):
        return None
    packages = payload.get("packages")
    if not isinstance(packages, list):
        return None
    crates: set[str] = set()
    entries: list[tuple[str, list[object]]] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        if not isinstance(name, str):
            continue
        crate = _normalize(name)
        crates.add(crate)
        dependencies = package.get("dependencies")
        if isinstance(dependencies, list):
            entries.append((crate, dependencies))
    external: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for crate, dependencies in entries:
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            package_name = dependency.get("name")
            if not isinstance(package_name, str):
                continue
            target = _normalize(package_name)
            rename = dependency.get("rename")
            key = _normalize(rename) if isinstance(rename, str) else target
            if target in crates:
                if dependency.get("kind") != "dev":
                    edges.add((crate, target))
            else:
                external.add(key)
    return CargoFacts(frozenset(crates), frozenset(external), frozenset(edges))
