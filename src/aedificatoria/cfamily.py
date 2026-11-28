"""C and C++ analysis backed by pinned tree-sitter grammars."""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_c
import tree_sitter_cpp
from tree_sitter import Language, Node, Parser, Tree

from .config import Config
from .git import tracked_files
from .model import TestInfo
from .paths import ConfinedPathError, MAX_SOURCE_BYTES, read_text
from .syntax import tree_sitter_error

NAME = "c_cpp"
EXTENSIONS = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".ixx")
_HEADERS = frozenset({".h", ".hpp", ".hh", ".hxx", ".ixx"})
_C_EXT = frozenset({".c", ".h"})
_CPP_EXT = frozenset({".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".ixx"})
_GTEST = frozenset({"TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P"})
_CATCH = frozenset({"TEST_CASE", "SCENARIO"})
_ASSERTIONS = ("EXPECT_", "ASSERT", "REQUIRE", "CHECK", "FAIL", "TEST_ASSERT")
_STDLIB = frozenset(
    {
        "algorithm", "any", "array", "atomic", "bitset", "cassert", "cctype",
        "cerrno", "cfenv", "cfloat", "chrono", "cinttypes", "climits", "clocale",
        "cmath", "codecvt", "complex", "condition_variable", "csetjmp", "csignal",
        "cstdarg", "cstddef", "cstdint", "cstdio", "cstdlib", "cstring", "ctime",
        "cuchar", "cwchar", "cwctype", "deque", "exception", "filesystem",
        "forward_list", "fstream", "functional", "future", "initializer_list",
        "iomanip", "ios", "iosfwd", "iostream", "istream", "iterator", "limits",
        "list", "locale", "map", "memory", "mutex", "new", "numeric", "optional",
        "ostream", "queue", "random", "ranges", "ratio", "regex", "set",
        "shared_mutex", "source_location", "span", "sstream", "stack", "stdexcept",
        "streambuf", "string", "string_view", "system_error", "thread", "tuple",
        "type_traits", "typeindex", "typeinfo", "unordered_map", "unordered_set",
        "utility", "valarray", "variant", "vector", "version",
        # C standard library
        "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h",
        "inttypes.h", "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h",
        "signal.h", "stdalign.h", "stdarg.h", "stdatomic.h", "stdbool.h",
        "stddef.h", "stdint.h", "stdio.h", "stdlib.h", "stdnoreturn.h",
        "string.h", "tgmath.h", "threads.h", "time.h", "uchar.h", "wchar.h",
        "wctype.h",
    }
)
_BRANCHES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "case_statement",
        "catch_clause",
        "conditional_expression",
    }
)
_COMMON_DIRS = {"include"}

_C_LANGUAGE = Language(tree_sitter_c.language())
_CPP_LANGUAGE = Language(tree_sitter_cpp.language())
_PARSERS = {"c": Parser(_C_LANGUAGE), "cpp": Parser(_CPP_LANGUAGE)}


@dataclass(frozen=True)
class CFile:
    tree: Tree
    path: str
    language: str


def _field(node: Node | None, name: str) -> Node | None:
    return node.child_by_field_name(name) if node is not None else None


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _error_count(node: Node) -> int:
    total = int(node.is_error)
    for child in node.named_children:
        if child.has_error:
            total += _error_count(child)
    return total


def _parse(language: str, path: str, text: str) -> CFile:
    tree = _PARSERS[language].parse(text.encode("utf-8"))
    return CFile(tree, path, language)


def parse_text(path: str, text: str | None) -> CFile | None:
    if text is None:
        return None
    suffix = Path(path).suffix
    if suffix in _C_EXT and suffix != ".h":
        return _parse("c", path, text)
    if suffix in _CPP_EXT:
        return _parse("cpp", path, text)
    if suffix == ".h":
        cpp = _parse("cpp", path, text)
        c = _parse("c", path, text)
        if cpp.tree.root_node.has_error and not c.tree.root_node.has_error:
            return c
        if _error_count(c.tree.root_node) < _error_count(cpp.tree.root_node):
            return c
        return cpp
    return None


def syntax_error(path: str, text: str | None) -> tuple[int, str, str] | None:
    parsed = parse_text(path, text)
    if parsed is None or text is None:
        return None
    return tree_sitter_error(parsed.tree, text)


def _name(node: Node | None) -> str:
    if node is None:
        return ""
    if node.type in {
        "identifier",
        "field_identifier",
        "type_identifier",
        "namespace_identifier",
        "destructor_name",
        "operator_name",
    }:
        return _text(node)
    declarator = _field(node, "declarator")
    if declarator is not None:
        return _name(declarator)
    return _text(node).split("::")[-1]


def _is_static(node: Node) -> bool:
    for child in node.named_children:
        if child.type == "storage_class_specifier" and _text(child) == "static":
            return True
    return False


def _function_signature(declaration: Node, declarator: Node) -> str:
    result = _text(_field(declaration, "type"))
    return _norm(result + _text(declarator))


def _add_function(
    declaration: Node,
    declarator: Node,
    prefix: str,
    api: dict[str, str],
) -> None:
    name = _name(declarator)
    if name:
        api[prefix + name] = _function_signature(declaration, declarator)


def _class_members(
    body: Node,
    prefix: str,
    public_by_default: bool,
    api: dict[str, str],
) -> None:
    public = public_by_default
    for child in body.named_children:
        if child.type == "access_specifier":
            public = _text(child).rstrip(":") == "public"
            continue
        if not public:
            continue
        if child.type == "field_declaration":
            declarator = _field(child, "declarator")
            if declarator is not None and declarator.type == "function_declarator":
                _add_function(child, declarator, prefix, api)
            else:
                name = _name(declarator)
                if name:
                    api[prefix + name] = "field:" + _norm(_text(_field(child, "type")))
        elif child.type in {"function_definition", "declaration"}:
            declarator = _field(child, "declarator")
            if declarator is not None and declarator.type == "function_declarator":
                _add_function(child, declarator, prefix, api)
        elif child.type in {"struct_specifier", "class_specifier", "union_specifier"}:
            _add_aggregate(child, prefix, api)


def _add_aggregate(node: Node, prefix: str, api: dict[str, str]) -> None:
    name = _name(_field(node, "name"))
    if not name:
        return
    key = prefix + name
    api[key] = node.type
    body = _field(node, "body")
    if body is not None:
        default_public = node.type != "class_specifier"
        _class_members(body, key + "::", default_public, api)


def _namespace_declarations(node: Node, prefix: str, api: dict[str, str]) -> None:
    name = _name(_field(node, "name"))
    body = _field(node, "body")
    if body is not None:
        _declarations(body, prefix + (name + "::" if name else ""), api)


def _linkage_declarations(node: Node, prefix: str, api: dict[str, str]) -> None:
    body = _field(node, "body")
    if body is not None:
        _declarations(body, prefix, api)


def _template_declarations(node: Node, prefix: str, api: dict[str, str]) -> None:
    for nested in node.named_children:
        if nested.type in {"declaration", "function_definition", "class_specifier"}:
            _declarations_one(nested, prefix, api)


def _typedef_declaration(node: Node, prefix: str, api: dict[str, str]) -> None:
    identifiers = [item for item in node.named_children if item.type == "type_identifier"]
    for nested in node.named_children:
        if nested.type in {"struct_specifier", "union_specifier", "enum_specifier"}:
            _add_aggregate(nested, prefix, api)
    if identifiers:
        api[prefix + _text(identifiers[-1])] = "typedef:" + _norm(_text(node))


def _declarations(container: Node, prefix: str, api: dict[str, str]) -> None:
    for child in container.named_children:
        kind = child.type
        if kind == "namespace_definition":
            _namespace_declarations(child, prefix, api)
        elif kind == "linkage_specification":
            _linkage_declarations(child, prefix, api)
        elif kind == "template_declaration":
            _template_declarations(child, prefix, api)
        elif kind in {"declaration", "function_definition"}:
            _declarations_one(child, prefix, api)
        elif kind in {"struct_specifier", "class_specifier", "union_specifier", "enum_specifier"}:
            _add_aggregate(child, prefix, api)
        elif kind == "type_definition":
            _typedef_declaration(child, prefix, api)
        elif kind in {"preproc_ifdef", "preproc_if", "preproc_else", "preproc_elif"}:
            _declarations(child, prefix, api)


def _declarations_one(node: Node, prefix: str, api: dict[str, str]) -> None:
    if _is_static(node):
        return
    declarator = _field(node, "declarator")
    if declarator is not None and declarator.type == "function_declarator":
        _add_function(node, declarator, prefix, api)
        return
    for child in node.named_children:
        if child.type in {"struct_specifier", "class_specifier", "union_specifier", "enum_specifier"}:
            _add_aggregate(child, prefix, api)


def public_api(tree: CFile | None) -> dict[str, str]:
    if tree is None or Path(tree.path).suffix not in _HEADERS:
        return {}
    api: dict[str, str] = {}
    _declarations(tree.tree.root_node, "", api)
    return api


def _branches(node: Node) -> int:
    total = 1 if node.type in _BRANCHES else 0
    if node.type == "binary_expression":
        operator = _field(node, "operator")
        if operator is not None and _text(operator) in {"&&", "||"}:
            total += 1
    for child in node.named_children:
        if child.type in {"function_definition", "lambda_expression"}:
            continue
        total += _branches(child)
    return total


def _collect_scores(container: Node, prefix: str, scores: dict[str, int]) -> None:
    for child in container.named_children:
        if child.type == "namespace_definition":
            name = _name(_field(child, "name"))
            body = _field(child, "body")
            if body is not None:
                _collect_scores(body, prefix + (name + "::" if name else ""), scores)
        elif child.type in {"class_specifier", "struct_specifier", "union_specifier"}:
            name = _name(_field(child, "name"))
            body = _field(child, "body")
            if body is not None and name:
                _collect_scores(body, prefix + name + "::", scores)
        elif child.type == "function_definition":
            name = _name(_field(child, "declarator"))
            body = _field(child, "body")
            if name and body is not None:
                scores[prefix + name] = 1 + _branches(body)
        elif child.type in {
            "declaration_list",
            "field_declaration_list",
            "linkage_specification",
            "preproc_ifdef",
            "preproc_if",
            "preproc_else",
            "preproc_elif",
        }:
            _collect_scores(child, prefix, scores)


def complexity(tree: CFile | None) -> dict[str, int]:
    if tree is None:
        return {}
    scores: dict[str, int] = {}
    _collect_scores(tree.tree.root_node, "", scores)
    return scores


def _call_name(node: Node | None) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "field_identifier"}:
        return _text(node)
    if node.type in {"qualified_identifier", "field_expression"}:
        return _text(node).split("::")[-1].split(".")[-1]
    return ""


def _first_string(node: Node | None) -> str:
    if node is None:
        return ""
    for child in node.named_children:
        if child.type == "string_literal":
            return _text(child).strip('"')
    return ""


def _assertions(node: Node) -> int:
    total = 0
    for child in node.named_children:
        if child.type == "call_expression":
            if _call_name(_field(child, "function")).startswith(_ASSERTIONS):
                total += 1
        total += _assertions(child)
    return total


def _gtest_title(node: Node) -> str:
    params = _field(_field(node, "declarator"), "parameters")
    if params is None:
        return ""
    names = [_text(child) for child in params.named_children]
    return " > ".join(name for name in names if name)


def _test_call(node: Node) -> tuple[str, str] | None:
    if node.type == "expression_statement":
        for child in node.named_children:
            if child.type == "call_expression":
                name = _call_name(_field(child, "function"))
                if name in _CATCH:
                    return name, _first_string(_field(child, "arguments"))
    return None


def _test_impl_name(node: Node) -> str:
    match = re.match(r"TEST_IMPL\s*\(\s*([A-Za-z_]\w*)", _text(node))
    return match.group(1) if match else ""


def _run_test_name(node: Node) -> str:
    if node.type != "expression_statement":
        return ""
    for child in node.named_children:
        if child.type != "call_expression":
            continue
        if _call_name(_field(child, "function")) != "RUN_TEST":
            continue
        arguments = _field(child, "arguments")
        if arguments is None:
            return ""
        for argument in arguments.named_children:
            if argument.type in {"identifier", "field_identifier"}:
                return _text(argument)
    return ""


def _record_test_definition(
    child: Node,
    prefix: str,
    functions: set[str],
    skips: set[str],
    counts: dict[str, int],
    bodies: dict[str, Node],
) -> bool:
    if child.type != "function_definition":
        return False
    name = _name(_field(child, "declarator"))
    impl = _test_impl_name(child)
    if impl:
        key = f"{prefix}{impl}"
        functions.add(key)
        counts[key] = _assertions(_field(child, "body") or child)
        return True
    if name in _GTEST:
        title = _gtest_title(child)
        key = f"{prefix}{title}" if title else f"{prefix}{name}"
        functions.add(key)
        counts[key] = _assertions(_field(child, "body") or child)
        if "DISABLED_" in title:
            skips.add(key)
        return True
    body = _field(child, "body")
    if body is not None:
        bodies[name] = body
        _collect_tests(body, prefix, functions, skips, counts, bodies)
    return False


def _collect_tests(
    container: Node,
    prefix: str,
    functions: set[str],
    skips: set[str],
    counts: dict[str, int],
    bodies: dict[str, Node],
) -> None:
    children = container.named_children
    index = 0
    while index < len(children):
        child = children[index]
        index += 1
        if _record_test_definition(child, prefix, functions, skips, counts, bodies):
            continue
        run_test = _run_test_name(child)
        if run_test:
            key = f"{prefix}{run_test}"
            functions.add(key)
            counts[key] = _assertions(bodies.get(run_test, child))
            continue
        call = _test_call(child)
        if call is not None and index < len(children):
            name, title = call
            body = children[index]
            if body.type == "compound_statement":
                index += 1
                key = f"{prefix}{title}"
                functions.add(key)
                counts[key] = _assertions(body)
                raw = _text(child)
                if any(tag in raw for tag in ("[.]", "[!hide]", "[.hide]")):
                    skips.add(key)
                continue
        if child.type in {
            "namespace_definition",
            "linkage_specification",
            "declaration_list",
            "class_specifier",
            "struct_specifier",
            "preproc_ifdef",
            "preproc_if",
            "preproc_else",
            "preproc_elif",
        }:
            _collect_tests(child, prefix, functions, skips, counts, bodies)


def test_info(tree: CFile | None) -> TestInfo:
    if tree is None:
        return TestInfo(frozenset(), 0, frozenset())
    functions: set[str] = set()
    skips: set[str] = set()
    counts: dict[str, int] = {}
    bodies: dict[str, Node] = {}
    _collect_tests(tree.tree.root_node, "", functions, skips, counts, bodies)
    return TestInfo(frozenset(functions), sum(counts.values()), frozenset(skips), counts)


def _includes(tree: CFile) -> list[tuple[str, bool, int]]:
    found: list[tuple[str, bool, int]] = []
    stack = [tree.tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "preproc_include":
            for child in node.named_children:
                if child.type == "system_lib_string":
                    found.append((_text(child).strip("<>"), True, node.start_point.row + 1))
                elif child.type == "string_literal":
                    found.append((_text(child).strip('"'), False, node.start_point.row + 1))
        stack.extend(reversed(node.named_children))
    return found


def _files(root: Path, config: Config) -> list[str]:
    found: list[str] = []
    for rel in tracked_files(root):
        if (
            config.is_excluded(rel)
            or config.is_test(rel)
            or config.is_metric_excluded(rel)
        ):
            continue
        full = root / rel
        if not rel.endswith(EXTENSIONS) or full.is_symlink():
            continue
        try:
            if full.stat().st_size <= MAX_SOURCE_BYTES:
                found.append(rel)
        except OSError:
            continue
    return found


def _component(path: str, config: Config) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    for root in config.source_roots:
        root = root.strip("/")
        if root and parts and parts[0] == root:
            parts = parts[1:]
            break
    if len(parts) > 1 and parts[0] in _COMMON_DIRS:
        parts = parts[1:]
    if not parts:
        return ""
    return parts[0] if len(parts) > 1 else Path(parts[0]).stem


def _local_components(root: Path, config: Config) -> dict[str, str]:
    result: dict[str, str] = {}
    for rel in _files(root, config):
        component = _component(rel, config)
        if component:
            result[component] = rel
    return result


def _target_file(root: Path, rel: str, include: str) -> str | None:
    base = posixpath.dirname(rel)
    for candidate in (posixpath.normpath(posixpath.join(base, include)), include):
        if (root / candidate).is_file():
            return candidate
    return None


def package_edges(root: Path, config: Config) -> tuple[set[tuple[str, str]], set[str]]:
    packages = set(_local_components(root, config))
    edges: set[tuple[str, str]] = set()
    for rel in _files(root, config):
        try:
            text = read_text(root, rel, errors="replace")
        except (OSError, UnicodeDecodeError, ConfinedPathError):
            continue
        tree = parse_text(rel, text)
        if tree is None:
            continue
        source = _component(rel, config)
        if not source:
            continue
        for include, system, _line in _includes(tree):
            if system:
                continue
            target = _target_file(root, rel, include)
            if (
                target is None
                or config.is_excluded(target)
                or config.is_test(target)
                or config.is_metric_excluded(target)
            ):
                continue
            target_component = _component(target, config)
            if target_component and target_component != source:
                edges.add((source, target_component))
    return edges, packages


def project_imports(root: Path, config: Config) -> dict[str, tuple[str, int]]:
    local = set(_local_components(root, config))
    found: dict[str, tuple[str, int]] = {}
    for rel in _files(root, config):
        try:
            text = read_text(root, rel, errors="replace")
        except (OSError, UnicodeDecodeError, ConfinedPathError):
            continue
        tree = parse_text(rel, text)
        if tree is None:
            continue
        for include, system, line in _includes(tree):
            if not system:
                continue
            module = include.split("/", 1)[0]
            if module in _STDLIB or module in local:
                continue
            found.setdefault(module, (rel, line))
    return found


def declared_dependencies(_root: Path, _config: Config) -> dict[str, tuple[str, int]]:
    return {}


def local_packages(root: Path, config: Config) -> set[str]:
    return set(_local_components(root, config))


def is_stdlib(module: str) -> bool:
    return module in _STDLIB
