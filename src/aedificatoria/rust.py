"""Rust analysis backed by the pinned tree-sitter Rust grammar.

This replaces the former hand-written lexer. The manifest/crate index stays
independent of parsing; item, API, complexity, and test analysis come from the
tree-sitter syntax tree.
"""

from __future__ import annotations

import os
import posixpath
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_rust
from tree_sitter import Language, Node, Parser, Tree

from .config import Config
from .git import tracked_files
from .model import ImportRef, TestInfo
from .paths import ConfinedPathError, MAX_SOURCE_BYTES, read_text
from .syntax import tree_sitter_error

NAME = "rust"
EXTENSIONS = (".rs",)

STDLIB = frozenset({"std", "core", "alloc", "proc_macro", "test"})

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

_FUNCTION_TYPES = frozenset({"function_item", "function_signature_item"})
_BRANCH_TYPES = frozenset(
    {
        "if_expression",
        "for_expression",
        "while_expression",
        "loop_expression",
        "match_arm",
    }
)
_LOGICAL = frozenset({"&&", "||"})
_TEST_ATTRS = frozenset({"test", "rstest", "test_case"})

_PARSER = Parser(Language(tree_sitter_rust.language()))


@dataclass(frozen=True)
class RustFile:
    tree: Tree
    path: str


def parse_text(_path: str, text: str | None) -> RustFile | None:
    if text is None:
        return None
    return RustFile(_PARSER.parse(text.encode("utf-8")), _path)


def syntax_error(text: str | None) -> tuple[int, str, str] | None:
    if text is None:
        return None
    return tree_sitter_error(_PARSER.parse(text.encode("utf-8")), text)


def _field(node: Node | None, name: str) -> Node | None:
    return node.child_by_field_name(name) if node is not None else None


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", "replace")


def _line(node: Node) -> int:
    return node.start_point.row + 1


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _visibility(node: Node) -> str:
    return next(
        (_text(child) for child in node.children if child.type == "visibility_modifier"),
        "",
    )


def _is_public(node: Node) -> bool:
    return _visibility(node) == "pub"


def _type_name(node: Node | None) -> str:
    if node is None:
        return ""
    name = _field(node, "name")
    if name is not None:
        return _text(name)
    inner = _field(node, "type")
    if inner is not None:
        return _type_name(inner)
    return _text(node)


def _parameter_signature(parameters: Node | None) -> str:
    if parameters is None:
        return "()"
    parts: list[str] = []
    for child in parameters.named_children:
        if child.type != "parameter":
            parts.append(_text(child))
            continue
        type_node = next(
            (
                named
                for named in reversed(child.named_children)
                if named.type not in {"identifier", "mutable_specifier", "self_parameter"}
            ),
            None,
        )
        parts.append(_text(type_node) if type_node is not None else _text(child))
    return "(" + ",".join(parts) + ")"


def _signature(node: Node) -> str:
    parts: list[str] = []
    type_parameters = _field(node, "type_parameters")
    if type_parameters is not None:
        parts.append(_text(type_parameters))
    parts.append(_parameter_signature(_field(node, "parameters")))
    returns = _field(node, "return_type")
    if returns is not None:
        parts.append("->" + _text(returns))
    return _norm("".join(parts))


def _key(module: tuple[str, ...], owner: str | None, name: str) -> str:
    parts = module + ((owner,) if owner else ()) + (name,)
    return "::".join(parts)


def _function_api(
    node: Node,
    module: tuple[str, ...],
    owner: str | None,
    default_public: bool,
    api: dict[str, str],
) -> None:
    if not default_public and not _is_public(node):
        return
    name = _field(node, "name")
    if name is not None:
        api[_key(module, owner, _text(name))] = "fn:" + _signature(node)


def _struct_api(node: Node, module: tuple[str, ...], api: dict[str, str]) -> None:
    if not _is_public(node):
        return
    name = _field(node, "name")
    if name is None:
        return
    key = _key(module, None, _text(name))
    type_parameters = _field(node, "type_parameters")
    api[key] = "struct:" + _norm(_text(type_parameters))
    body = _field(node, "body")
    if body is None:
        return
    for field in body.named_children:
        if field.type != "field_declaration" or _visibility(field) != "pub":
            continue
        field_name = _field(field, "name")
        if field_name is not None:
            api[f"{key}::{_text(field_name)}"] = "field:" + _norm(_text(_field(field, "type")))


def _enum_api(node: Node, module: tuple[str, ...], api: dict[str, str]) -> None:
    if not _is_public(node):
        return
    name = _field(node, "name")
    if name is None:
        return
    key = _key(module, None, _text(name))
    api[key] = "enum"
    body = _field(node, "body")
    if body is None:
        return
    for variant in body.named_children:
        if variant.type != "enum_variant":
            continue
        variant_name = _field(variant, "name")
        if variant_name is not None:
            api[f"{key}::{_text(variant_name)}"] = "variant"


def _named_item_api(node: Node, module: tuple[str, ...], api: dict[str, str]) -> None:
    if not _is_public(node):
        return
    name = _field(node, "name")
    if name is None:
        return
    kind = node.type.removesuffix("_item")
    api[_key(module, None, _text(name))] = f"{kind}:" + _norm(_text(_field(node, "type")))


def _collect_public_types(
    container: Node,
    module: tuple[str, ...],
    names: set[str],
) -> None:
    for child in container.named_children:
        kind = child.type
        if kind == "mod_item":
            name = _field(child, "name")
            body = _field(child, "body")
            if name is not None and body is not None:
                _collect_public_types(body, module + (_text(name),), names)
        elif kind in {"struct_item", "enum_item", "type_item"} and _is_public(child):
            name = _field(child, "name")
            if name is not None:
                names.add(_key(module, None, _text(name)))


def _trait_api(
    node: Node,
    module: tuple[str, ...],
    public_types: set[str],
    api: dict[str, str],
) -> None:
    if not _is_public(node):
        return
    name = _field(node, "name")
    body = _field(node, "body")
    if name is None:
        return
    key = _key(module, None, _text(name))
    api[key] = "trait"
    if body is not None:
        _collect_api(body, module, _text(name), True, public_types, api)


def _item_api(
    node: Node,
    module: tuple[str, ...],
    owner: str | None,
    default_public: bool,
    api: dict[str, str],
) -> None:
    kind = node.type
    if kind in _FUNCTION_TYPES:
        _function_api(node, module, owner, default_public, api)
    elif kind == "struct_item":
        _struct_api(node, module, api)
    elif kind == "enum_item":
        _enum_api(node, module, api)
    elif kind in {"const_item", "static_item", "type_item"}:
        _named_item_api(node, module, api)


def _collect_api(
    container: Node,
    module: tuple[str, ...],
    owner: str | None,
    default_public: bool,
    public_types: set[str],
    api: dict[str, str],
) -> None:
    for child in container.named_children:
        kind = child.type
        if kind == "mod_item":
            name = _field(child, "name")
            body = _field(child, "body")
            if name is not None and body is not None:
                _collect_api(
                    body,
                    module + (_text(name),),
                    None,
                    False,
                    public_types,
                    api,
                )
        elif kind == "impl_item":
            impl_owner = _type_name(_field(child, "type"))
            body = _field(child, "body")
            if (
                impl_owner
                and body is not None
                and _key(module, None, impl_owner) in public_types
            ):
                _collect_api(body, module, impl_owner, False, public_types, api)
        elif kind == "trait_item":
            _trait_api(child, module, public_types, api)
        elif kind in {
            "function_item",
            "function_signature_item",
            "struct_item",
            "enum_item",
            "const_item",
            "static_item",
            "type_item",
        }:
            _item_api(child, module, owner, default_public, api)
        elif kind in {"source_file", "declaration_list"}:
            _collect_api(child, module, owner, default_public, public_types, api)


def public_api(tree: RustFile | None) -> dict[str, str]:
    if tree is None:
        return {}
    public_types: set[str] = set()
    _collect_public_types(tree.tree.root_node, (), public_types)
    api: dict[str, str] = {}
    _collect_api(tree.tree.root_node, (), None, False, public_types, api)
    return api


def _branches(node: Node) -> int:
    total = 0
    if node.type in _BRANCH_TYPES:
        total += 1
    elif node.type == "binary_expression":
        operator = _field(node, "operator")
        if operator is not None and _text(operator) in _LOGICAL:
            total += 1
    for child in node.named_children:
        if child.type in _FUNCTION_TYPES:
            continue
        total += _branches(child)
    return total


def _record_function(
    node: Node,
    module: tuple[str, ...],
    owner: str | None,
    scores: dict[str, int],
) -> None:
    name = _field(node, "name")
    body = _field(node, "body")
    if name is None or body is None:
        return
    scores[_key(module, owner, _text(name))] = 1 + _branches(body)


def _collect_scores(
    container: Node,
    module: tuple[str, ...],
    owner: str | None,
    scores: dict[str, int],
) -> None:
    for child in container.named_children:
        kind = child.type
        if kind == "mod_item":
            name = _field(child, "name")
            body = _field(child, "body")
            if name is not None and body is not None:
                _collect_scores(body, module + (_text(name),), None, scores)
        elif kind == "impl_item":
            impl_owner = _type_name(_field(child, "type"))
            body = _field(child, "body")
            if impl_owner and body is not None:
                _collect_scores(body, module, impl_owner, scores)
        elif kind == "trait_item":
            name = _field(child, "name")
            body = _field(child, "body")
            if name is not None and body is not None:
                _collect_scores(body, module, _text(name), scores)
        elif kind == "function_item":
            _record_function(child, module, owner, scores)
            body = _field(child, "body")
            if body is not None:
                _collect_scores(body, module, None, scores)
        elif kind in {"source_file", "declaration_list", "block"}:
            _collect_scores(child, module, owner, scores)


def complexity(tree: RustFile | None) -> dict[str, int]:
    if tree is None:
        return {}
    scores: dict[str, int] = {}
    _collect_scores(tree.tree.root_node, (), None, scores)
    return scores


def _strip_attr(text: str) -> str:
    body = text.strip()
    body = body[2:] if body.startswith("#[") else body
    body = body[:-1] if body.endswith("]") else body
    return body.split("(", 1)[0]


def _is_test_attr(text: str) -> bool:
    parts = [part for part in re.split(r"[^A-Za-z0-9_]+", _strip_attr(text)) if part]
    return bool(parts) and parts[-1] in _TEST_ATTRS


def _is_ignore_attr(text: str) -> bool:
    return "ignore" in re.split(r"[^a-z0-9_]+", text.lower())


def _assertion_nodes(node: Node) -> int:
    total = 0
    if node.type == "macro_invocation":
        macro = _field(node, "macro")
        name = _text(macro)
        if name.startswith("assert") or name.startswith("prop_assert"):
            total += 1
    for child in node.named_children:
        total += _assertion_nodes(child)
    return total


def _collect_tests(
    container: Node,
    module: tuple[str, ...],
    inherited_skip: bool,
    functions: set[str],
    skips: set[str],
    nodes: list[tuple[str, Node]],
) -> None:
    attributes: list[str] = []
    for child in container.named_children:
        if child.type == "attribute_item":
            attributes.append(_text(child))
            continue
        if child.type in {"line_comment", "block_comment"}:
            continue
        if child.type == "function_item":
            if any(_is_test_attr(attribute) for attribute in attributes):
                name = _field(child, "name")
                if name is not None:
                    key = _key(module, None, _text(name))
                    functions.add(key)
                    if inherited_skip or any(_is_ignore_attr(attr) for attr in attributes):
                        skips.add(key)
                    nodes.append((key, child))
            attributes = []
            continue
        if child.type == "mod_item":
            name = _field(child, "name")
            body = _field(child, "body")
            if name is not None and body is not None:
                _collect_tests(
                    body,
                    module + (_text(name),),
                    inherited_skip,
                    functions,
                    skips,
                    nodes,
                )
            attributes = []
            continue
        if child.type in {"source_file", "declaration_list", "impl_item", "trait_item"}:
            _collect_tests(child, module, inherited_skip, functions, skips, nodes)
            attributes = []
            continue
        attributes = []


def test_info(tree: RustFile | None) -> TestInfo:
    if tree is None:
        return TestInfo(frozenset(), 0, frozenset())
    functions: set[str] = set()
    skips: set[str] = set()
    nodes: list[tuple[str, Node]] = []
    _collect_tests(tree.tree.root_node, (), False, functions, skips, nodes)
    counts = {key: _assertion_nodes(node) for key, node in nodes}
    return TestInfo(frozenset(functions), sum(counts.values()), frozenset(skips), counts)


def _use_root(node: Node | None) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "type_identifier", "crate", "self", "super"}:
        return _text(node)
    if node.type in {"scoped_identifier", "scoped_type_identifier"}:
        path = _field(node, "path")
        return _use_root(path) if path is not None else _text(node).split("::")[0]
    if node.type in {"scoped_use_list", "use_as_clause"}:
        return _use_root(_field(node, "path"))
    if node.type == "use_wildcard":
        for child in node.named_children:
            root = _use_root(child)
            if root:
                return root
    if node.type == "use_list":
        for child in node.named_children:
            root = _use_root(child)
            if root:
                return root
    return ""


def _collect_imports(node: Node, specs: list[tuple[str, int]]) -> None:
    if node.type == "use_declaration":
        root = _use_root(_field(node, "argument"))
        if root:
            specs.append((root, _line(node)))
    elif node.type == "extern_crate_declaration":
        name = _field(node, "name")
        if name is not None:
            specs.append((_text(name), _line(node)))
    for child in node.named_children:
        _collect_imports(child, specs)


def imports(tree: RustFile) -> list[ImportRef]:
    specs: list[tuple[str, int]] = []
    _collect_imports(tree.tree.root_node, specs)
    refs: list[ImportRef] = []
    seen: set[str] = set()
    for module, line in specs:
        if module in {"crate", "self", "super", "*"} or module in seen:
            continue
        seen.add(module)
        refs.append(ImportRef(module=module, level=0, line=line))
    return refs


def is_stdlib(module: str) -> bool:
    return module in STDLIB


def _read_manifest(root: Path, rel: str) -> tuple[str, dict[str, tuple[str, int]]]:
    try:
        text = read_text(root, rel)
    except (OSError, UnicodeDecodeError, ConfinedPathError):
        return "", {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        data = {}
    name = ""
    package = data.get("package")
    if isinstance(package, dict) and isinstance(package.get("name"), str):
        name = package["name"].replace("-", "_")
    deps: dict[str, tuple[str, int]] = {}
    table = ""
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        header = re.match(r"^\[(.+?)\]\s*$", stripped)
        if header:
            table = header.group(1).strip().strip('"')
            member = re.match(
                r"^(?:workspace\.|target\..+\.)?(?:dev-|build-)?dependencies\.([A-Za-z0-9_-]+)$",
                table,
            )
            if member:
                deps.setdefault(member.group(1).replace("-", "_"), (rel, number))
            continue
        if not _is_dependency_table(table):
            continue
        key = re.match(r"""^["']?([A-Za-z0-9_.-]+)["']?\s*=""", stripped)
        if key:
            dependency = key.group(1).replace("-", "_").split(".", 1)[0]
            deps.setdefault(dependency, (rel, number))
    return name, deps


def declared_dependencies(root: Path, _config: Config) -> dict[str, tuple[str, int]]:
    manifests, _ = _repo_files(root)
    found: dict[str, tuple[str, int]] = {}
    for rel in manifests:
        if (
            _config.is_excluded(rel)
            or _config.is_test(rel)
            or _config.is_metric_excluded(rel)
        ):
            continue
        _, deps = _read_manifest(root, rel)
        for dependency, site in deps.items():
            found.setdefault(dependency, site)
    return found


def _is_dependency_table(table: str) -> bool:
    return (
        re.fullmatch(
            r"(?:workspace\.|target\..+\.)?(?:dev-|build-)?dependencies",
            table,
        )
        is not None
    )


def _repo_files(root: Path) -> tuple[list[str], list[str]]:
    manifests: list[str] = []
    sources: list[str] = []
    for rel in tracked_files(root):
        name = posixpath.basename(rel)
        full = root / rel
        if full.is_symlink():
            continue
        try:
            if full.stat().st_size > MAX_SOURCE_BYTES:
                continue
        except OSError:
            continue
        if name == "Cargo.toml":
            manifests.append(rel)
        elif name.endswith(".rs"):
            sources.append(rel)
    return manifests, sources


def _collect_modules(node: Node, modules: set[str]) -> None:
    if node.type == "mod_item":
        name = _field(node, "name")
        if name is not None:
            modules.add(_text(name))
    for child in node.named_children:
        _collect_modules(child, modules)


class RepoIndex:
    """Crate names, module names, declared dependencies, and parsed sources."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.crates: dict[str, str] = {}
        self.modules: set[str] = set()
        self.deps: dict[str, tuple[str, int]] = {}
        self.files: dict[str, RustFile] = {}
        self._scan()

    def _scan(self) -> None:
        manifests, sources = _repo_files(self.root)
        for rel in manifests:
            name, deps = _read_manifest(self.root, rel)
            if name:
                self.crates[posixpath.dirname(rel)] = name
            for dependency, site in deps.items():
                self.deps.setdefault(dependency, site)
        for rel in sources:
            try:
                text = read_text(self.root, rel)
            except (OSError, UnicodeDecodeError, ConfinedPathError):
                continue
            tree = parse_text(rel, text)
            if tree is None:
                continue
            self.files[rel] = tree
            self.modules.add(Path(rel).stem)
            _collect_modules(tree.tree.root_node, self.modules)

    def crate_of(self, rel: str) -> str:
        directory = posixpath.dirname(rel)
        while True:
            name = self.crates.get(directory)
            if name:
                return name
            if not directory:
                return ""
            directory = posixpath.dirname(directory)


def _usable(rel: str, config: Config) -> bool:
    return (
        not config.is_excluded(rel)
        and not config.is_test(rel)
        and not config.is_metric_excluded(rel)
    )


def package_edges(root: Path, config: Config) -> tuple[set[tuple[str, str]], set[str]]:
    index = RepoIndex(root)
    files = {rel: tree for rel, tree in index.files.items() if _usable(rel, config)}
    packages = {
        package for rel in files if (package := index.crate_of(rel))
    }
    edges: set[tuple[str, str]] = set()
    for rel, tree in files.items():
        package = index.crate_of(rel)
        if not package:
            continue
        for ref in imports(tree):
            if ref.module in packages and ref.module != package:
                edges.add((package, ref.module))
    return edges, packages


def project_imports(root: Path, config: Config) -> dict[str, tuple[str, int]]:
    index = RepoIndex(root)
    found: dict[str, tuple[str, int]] = {
        dependency: site
        for dependency, site in index.deps.items()
        if _usable(site[0], config)
    }
    for rel, tree in index.files.items():
        if not _usable(rel, config):
            continue
        for ref in imports(tree):
            if ref.level != 0 or not ref.module:
                continue
            found.setdefault(ref.module, (rel, ref.line))
    return found


def local_packages(root: Path, _config: Config) -> set[str]:
    index = RepoIndex(root)
    return set(index.crates.values()) | {
        Path(rel).stem for rel in index.files if _usable(rel, _config)
    }
