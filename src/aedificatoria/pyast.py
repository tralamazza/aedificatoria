"""Python AST helpers: imports, API surface, complexity, tests, package graph."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from .config import Config
from .git import tracked_python_files
from .model import ImportRef, TestInfo

NAME = "python"
EXTENSIONS = (".py",)


def is_stdlib(module: str) -> bool:
    return module in sys.stdlib_module_names


def parse_text(_path: str, text: str | None) -> ast.Module | None:
    if not text:
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def imports(tree: ast.Module) -> list[ImportRef]:
    found: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top:
                    found.append(ImportRef(module=top, level=0, line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            found.append(ImportRef(module=node.module or "", level=node.level, line=node.lineno))
    return found


def _normalize_signature(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _func_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    returns = f"->{ast.unparse(node.returns)}" if node.returns else ""
    return _normalize_signature(f"({args}){returns}")


def _class_signature(node: ast.ClassDef) -> str:
    parts = [ast.unparse(base) for base in node.bases]
    parts.extend(f"{kw.arg}={ast.unparse(kw.value)}" for kw in node.keywords if kw.arg)
    return _normalize_signature("class(" + ",".join(parts) + ")")


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    return []


def _string_set(node: ast.expr | None) -> set[str] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: set[str] = set()
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                values.add(element.value)
            else:
                return None
        return values
    return None


def declared_all(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return _string_set(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            return _string_set(node.value)
    return None


_PUBLIC_METHODS = {"__init__", "__call__", "__enter__", "__exit__"}


def _function_surface(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    if node.name.startswith("_"):
        return None
    return _func_signature(node)


def _class_entries(node: ast.ClassDef) -> dict[str, str]:
    if node.name.startswith("_"):
        return {}
    entries = {node.name: _class_signature(node)}
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            not item.name.startswith("_") or item.name in _PUBLIC_METHODS
        ):
            entries[f"{node.name}.{item.name}"] = _func_signature(item)
    return entries


def _assignment_entries(targets: list[ast.expr]) -> dict[str, str]:
    entries: dict[str, str] = {}
    for target in targets:
        for name in _target_names(target):
            if not name.startswith("_") and name != "__all__":
                entries[name] = "const"
    return entries


def public_api(tree: ast.Module) -> dict[str, str]:
    """Return public symbols -> normalized signature/kind."""
    surface: dict[str, str] = {}
    exported = declared_all(tree)
    if exported is not None:
        for name in sorted(exported):
            surface[name] = "exported"

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signature = _function_surface(node)
            if signature is not None:
                surface[node.name] = signature
        elif isinstance(node, ast.ClassDef):
            surface.update(_class_entries(node))
        elif isinstance(node, ast.Assign):
            surface.update(_assignment_entries(node.targets))
        elif isinstance(node, ast.AnnAssign):
            surface.update(_assignment_entries([node.target]))
    return surface


class _ComplexityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.scores: dict[str, int] = {}

    def _key(self) -> str:
        return ".".join(self.stack)

    def _add(self, amount: int = 1) -> None:
        if not self.stack:
            return
        key = self._key()
        if key in self.scores:
            self.scores[key] += amount

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        key = ".".join(self.stack + [node.name])
        self.scores[key] = 1
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_If(self, node: ast.If) -> None:
        self._add()
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._add()
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._add()
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._add()
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._add()
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._add()
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self._add(max(1, len(node.values) - 1))
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._add(1 + len(node.ifs))
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self._add(len(node.cases))
        self.generic_visit(node)


def function_complexity(tree: ast.Module) -> dict[str, int]:
    visitor = _ComplexityVisitor()
    visitor.visit(tree)
    return visitor.scores


def _has_skip(decorators: list[ast.expr]) -> bool:
    for decorator in decorators:
        for node in ast.walk(decorator):
            if isinstance(node, ast.Name) and node.id.lower() in {"skip", "skipif", "xfail"}:
                return True
            if isinstance(node, ast.Attribute) and node.attr.lower() in {"skip", "skipif", "xfail"}:
                return True
    return False


def _call_name(node: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _call_assertion(node: ast.Call) -> bool:
    name = _call_name(node.func)
    last = name.rsplit(".", 1)[-1] if name else ""
    return last.startswith("assert") or last in {"raises", "fail"}


def _call_skip(node: ast.Call) -> bool:
    name = _call_name(node.func)
    last = name.rsplit(".", 1)[-1] if name else ""
    return name in {"pytest.skip", "pytest.xfail", "unittest.skip"} or last == "skipTest"


def _walk_assertions(tree: ast.Module, skips: set[str]) -> int:
    assertions = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            assertions += 1
        elif isinstance(node, ast.Call):
            if _call_assertion(node):
                assertions += 1
            if _call_skip(node):
                skips.add("<module>")
    return assertions


def _assertion_count(node: ast.AST) -> int:
    total = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            total += 1
        elif isinstance(child, ast.Call) and _call_assertion(child):
            total += 1
    return total


def _collect_tests(
    body: list[ast.stmt],
    prefix: str,
    functions: set[str],
    skips: set[str],
    counts: dict[str, int],
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            name = f"{prefix}{node.name}"
            functions.add(name)
            counts[name] = _assertion_count(node)
            if _has_skip(node.decorator_list):
                skips.add(name)
        elif isinstance(node, ast.ClassDef):
            class_name = f"{prefix}{node.name}"
            if _has_skip(node.decorator_list):
                skips.add(class_name)
            _collect_tests(node.body, class_name + ".", functions, skips, counts)


def _module_marker_skips(tree: ast.Module, skips: set[str]) -> None:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "pytestmark"):
                continue
            marker = ast.unparse(node.value).lower()
            if "skip" in marker or "xfail" in marker:
                skips.add("<module>")


def test_info(tree: ast.Module | None) -> TestInfo:
    if tree is None:
        return TestInfo(frozenset(), 0, frozenset())
    functions: set[str] = set()
    skips: set[str] = set()
    counts: dict[str, int] = {}
    assertions = _walk_assertions(tree, skips)
    _collect_tests(tree.body, "", functions, skips, counts)
    _module_marker_skips(tree, skips)
    return TestInfo(frozenset(functions), assertions, frozenset(skips), counts)


def declared_dependencies(_root: Path, _config: Config) -> dict[str, tuple[str, int]]:
    return {}


def local_packages(root: Path, config: Config) -> set[str]:
    packages = {
        config.package_of(path)
        for path in tracked_python_files(root, config)
        if not config.is_test(path)
    }
    packages.discard("")
    return packages


def project_imports(root: Path, config: Config) -> dict[str, tuple[str, int]]:
    """Absolute third-party/local imports across production Python files.

    Returns the first observed `(path, line)` for each top-level module.
    """
    found: dict[str, tuple[str, int]] = {}
    for rel in tracked_python_files(root, config):
        if config.is_test(rel):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tree = parse_text(rel, text)
        if tree is None:
            continue
        for ref in imports(tree):
            if ref.level != 0 or not ref.module:
                continue
            top = ref.module.split(".")[0]
            if not top or top == "__future__":
                continue
            found.setdefault(top, (rel, ref.line))
    return found


def package_edges(root: Path, config: Config) -> tuple[set[tuple[str, str]], set[str]]:
    """Build a top-level package dependency graph.

    Relative imports are treated as internal to their top-level package, so
    only absolute cross-package imports become edges. That is the right
    granularity for architectural cycles and layer violations.
    """
    files = [rel for rel in tracked_python_files(root, config) if not config.is_test(rel)]
    packages = {config.package_of(rel) for rel in files}
    packages.discard("")
    edges: set[tuple[str, str]] = set()

    for rel in files:
        package = config.package_of(rel)
        if not package:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tree = parse_text(rel, text)
        if tree is None:
            continue
        for ref in imports(tree):
            if ref.level != 0 or not ref.module:
                continue
            target = ref.module.split(".")[0]
            if target in packages and target != package:
                edges.add((package, target))
    return edges, packages


def strongly_connected(edges: set[tuple[str, str]], nodes: set[str]) -> list[tuple[str, ...]]:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        if source in graph and target in graph:
            graph[source].append(target)

    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for child in graph[node]:
            if child not in indices:
                visit(child)
                low[node] = min(low[node], low[child])
            elif child in on_stack:
                low[node] = min(low[node], indices[child])
        if low[node] == indices[node]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                components.append(tuple(sorted(component)))

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return sorted(components)


def cycle_evidence(component: tuple[str, ...], edges: set[tuple[str, str]]) -> str:
    members = set(component)
    internal = sorted((a, b) for a, b in edges if a in members and b in members)
    if internal:
        return "; ".join(f"{a} -> {b}" for a, b in internal[:8])
    return " -> ".join(component)
