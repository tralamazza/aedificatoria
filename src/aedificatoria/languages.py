"""Language adapters: file analysis and repository graphs per language."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import cfamily, jsts, pyast, rust
from .config import Config
from .model import TestInfo


@dataclass(frozen=True)
class Adapter:
    name: str
    extensions: tuple[str, ...]
    parse_text: Callable[[str, str | None], Any]
    public_api: Callable[[Any], dict[str, str]]
    complexity: Callable[[Any], dict[str, int]]
    test_info: Callable[[Any], TestInfo]
    is_stdlib: Callable[[str], bool]
    package_edges: Callable[[Path, Config], tuple[set[tuple[str, str]], set[str]]]
    project_imports: Callable[[Path, Config], dict[str, tuple[str, int]]]
    declared_dependencies: Callable[[Path, Config], dict[str, tuple[str, int]]]
    local_packages: Callable[[Path, Config], set[str]]


ADAPTERS: tuple[Adapter, ...] = (
    Adapter(
        name="python",
        extensions=pyast.EXTENSIONS,
        parse_text=pyast.parse_text,
        public_api=pyast.public_api,
        complexity=pyast.function_complexity,
        test_info=pyast.test_info,
        is_stdlib=pyast.is_stdlib,
        package_edges=pyast.package_edges,
        project_imports=pyast.project_imports,
        declared_dependencies=pyast.declared_dependencies,
        local_packages=pyast.local_packages,
    ),
    Adapter(
        name="rust",
        extensions=rust.EXTENSIONS,
        parse_text=rust.parse_text,
        public_api=rust.public_api,
        complexity=rust.complexity,
        test_info=rust.test_info,
        is_stdlib=rust.is_stdlib,
        package_edges=rust.package_edges,
        project_imports=rust.project_imports,
        declared_dependencies=rust.declared_dependencies,
        local_packages=rust.local_packages,
    ),
    Adapter(
        name="javascript",
        extensions=jsts.EXTENSIONS,
        parse_text=jsts.parse_text,
        public_api=jsts.public_api,
        complexity=jsts.complexity,
        test_info=jsts.test_info,
        is_stdlib=jsts.is_stdlib,
        package_edges=jsts.package_edges,
        project_imports=jsts.project_imports,
        declared_dependencies=jsts.declared_dependencies,
        local_packages=jsts.local_packages,
    ),
    Adapter(
        name=cfamily.NAME,
        extensions=cfamily.EXTENSIONS,
        parse_text=cfamily.parse_text,
        public_api=cfamily.public_api,
        complexity=cfamily.complexity,
        test_info=cfamily.test_info,
        is_stdlib=cfamily.is_stdlib,
        package_edges=cfamily.package_edges,
        project_imports=cfamily.project_imports,
        declared_dependencies=cfamily.declared_dependencies,
        local_packages=cfamily.local_packages,
    ),
)

_BY_NAME = {adapter.name: adapter for adapter in ADAPTERS}


def get(path: str) -> Adapter | None:
    for adapter in ADAPTERS:
        if path.endswith(adapter.extensions):
            return adapter


def syntax_error(
    adapter: Adapter, path: str, text: str | None
) -> tuple[int, str, str] | None:
    if adapter.name == "python":
        return pyast.syntax_error(text)
    if adapter.name == "rust":
        return rust.syntax_error(text)
    if adapter.name == "javascript":
        return jsts.syntax_error(path, text)
    if adapter.name == cfamily.NAME:
        return cfamily.syntax_error(path, text)
    return None
    return None


def abi_surface(adapter: Adapter, path: str, text: str | None) -> dict[str, str]:
    if adapter.name == cfamily.NAME:
        return cfamily.abi_surface(path, text)
    return {}


def names() -> list[str]:
    return [adapter.name for adapter in ADAPTERS]


def extensions() -> tuple[str, ...]:
    return tuple(extension for adapter in ADAPTERS for extension in adapter.extensions)


def same_language(first: str, second: str) -> bool:
    left = get(first)
    right = get(second)
    return left is not None and left is right


def package_graph(root: Path, config: Config) -> tuple[set[tuple[str, str]], set[str]]:
    edges: set[tuple[str, str]] = set()
    packages: set[str] = set()
    for adapter in ADAPTERS:
        adapter_edges, adapter_packages = adapter.package_edges(root, config)
        edges |= adapter_edges
        packages |= adapter_packages
    return edges, packages


def project_imports(root: Path, config: Config) -> dict[str, tuple[str, int, str]]:
    """Map each imported module to (path, line, language) for the first site."""
    found: dict[str, tuple[str, int, str]] = {}
    for adapter in ADAPTERS:
        for module, (path, line) in adapter.project_imports(root, config).items():
            found.setdefault(module, (path, line, adapter.name))
    return found


def local_packages(root: Path, config: Config, language: str) -> set[str]:
    adapter = _BY_NAME.get(language)
    if adapter is None:
        return set()
    return adapter.local_packages(root, config)


def declared_dependencies(
    root: Path, config: Config, language: str
) -> dict[str, tuple[str, int]]:
    adapter = _BY_NAME.get(language)
    if adapter is None:
        return {}
    return adapter.declared_dependencies(root, config)


def is_stdlib(language: str, module: str) -> bool:
    adapter = _BY_NAME.get(language)
    return adapter is not None and adapter.is_stdlib(module)
