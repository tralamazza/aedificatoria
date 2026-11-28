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
EXTENSIONS = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".ixx", ".cppm", ".mpp")
_HEADERS = frozenset({".h", ".hpp", ".hh", ".hxx", ".ixx", ".cppm", ".mpp"})
_C_EXT = frozenset({".c", ".h"})
_CPP_EXT = frozenset({".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".ixx", ".cppm", ".mpp"})
_MODULE_EXT = frozenset({".ixx", ".cppm", ".mpp"})
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
_OVERLOAD_PREFIX = "overloads:"
_OVERLOAD_SEPARATOR = "\x1f"

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


_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_MODULE_RE = re.compile(
    r"(?m)^[ \t]*(?:export\s+)?module\s+[A-Za-z_][\w.]*(?::[A-Za-z_]\w*)?\s*;"
)
_MODULE_LINE_RE = re.compile(
    r"(?m)^[ \t]*(?:export\s+)?module\s+[A-Za-z_][\w.]*(?::[A-Za-z_]\w*)?\s*;[ \t]*"
)
_MODULE_EMPTY_RE = re.compile(r"(?m)^[ \t]*module\s*;[ \t]*")
_MODULE_PRIVATE_RE = re.compile(r"(?m)^[ \t]*module\s*:\s*private\s*;[ \t]*")
_MODULE_IDENTITY_RE = re.compile(
    r"(?m)^[ \t]*export\s+module\s+([A-Za-z_][\w.]*(?::[A-Za-z_]\w*)?)\s*;"
)
_EXPORT_IMPORT_RE = re.compile(r"(?m)^[ \t]*export\s+import\s+([^;]+);")
_IMPORT_LINE_RE = re.compile(r"(?m)^[ \t]*(?:export\s+)?import\s+[^;]*;")
_EXPORT_RE = re.compile(r"(?m)^[ \t]*export\b")


def _semantic_text(text: str) -> str:
    return _norm(_COMMENT_RE.sub("", text))


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
    syntax_text = _module_parse_text(path, text)
    if syntax_text != text:
        parsed = parse_text(path, syntax_text)
    return tree_sitter_error(parsed.tree, syntax_text)


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


def _merge_signature(api: dict[str, str], key: str, signature: str) -> None:
    previous = api.get(key)
    if previous is None:
        api[key] = signature
        return
    if previous == signature:
        return
    if previous.startswith(_OVERLOAD_PREFIX):
        signatures = previous[len(_OVERLOAD_PREFIX) :].split(_OVERLOAD_SEPARATOR)
    else:
        signatures = [previous]
    if signature not in signatures:
        signatures.append(signature)
    if len(signatures) == 1:
        api[key] = signatures[0]
    else:
        api[key] = _OVERLOAD_PREFIX + _OVERLOAD_SEPARATOR.join(sorted(signatures))


def _add_function(
    declaration: Node,
    declarator: Node,
    prefix: str,
    api: dict[str, str],
    template_params: str = "",
) -> None:
    name = _name(declarator)
    if name:
        signature = _function_signature(declaration, declarator)
        if template_params:
            signature = "template" + template_params + ":" + signature
        _merge_signature(api, prefix + name, signature)


def _add_concept(
    node: Node, prefix: str, api: dict[str, str], template_params: str = ""
) -> None:
    name = _name(_field(node, "name"))
    if name:
        signature = "concept:" + template_params + ":" + _semantic_text(_text(node))
        _merge_signature(api, prefix + name, signature)


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


def _add_aggregate(
    node: Node, prefix: str, api: dict[str, str], template_params: str = ""
) -> None:
    name = _name(_field(node, "name"))
    if not name:
        return
    key = prefix + name
    value = node.type
    if template_params:
        value += ":" + template_params
    api[key] = value
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
    template_params = ""
    for child in node.named_children:
        if child.type == "template_parameter_list":
            template_params = _norm(_text(child))
            break
    for nested in node.named_children:
        if nested.type in {
            "declaration",
            "function_definition",
            "concept_definition",
            "class_specifier",
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
        }:
            if nested.type == "concept_definition":
                _add_concept(nested, prefix, api, template_params)
            else:
                _declarations_one(nested, prefix, api, template_params)


def _typedef_declaration(node: Node, prefix: str, api: dict[str, str]) -> None:
    identifiers = [item for item in node.named_children if item.type == "type_identifier"]
    record = next(
        (
            nested
            for nested in node.named_children
            if nested.type in {"struct_specifier", "union_specifier", "enum_specifier"}
        ),
        None,
    )
    for nested in node.named_children:
        if nested.type in {"struct_specifier", "union_specifier", "enum_specifier"}:
            _add_aggregate(nested, prefix, api)
    if identifiers:
        value = "typedef:" + (record.type if record is not None else _norm(_text(node)))
        api[prefix + _text(identifiers[-1])] = value


def _declarations(container: Node, prefix: str, api: dict[str, str]) -> None:
    for child in container.named_children:
        kind = child.type
        if kind == "namespace_definition":
            _namespace_declarations(child, prefix, api)
        elif kind == "linkage_specification":
            _linkage_declarations(child, prefix, api)
        elif kind == "template_declaration":
            _template_declarations(child, prefix, api)
        elif kind == "concept_definition":
            _add_concept(child, prefix, api)
        elif kind in {"declaration", "function_definition"}:
            _declarations_one(child, prefix, api)
        elif kind in {"struct_specifier", "class_specifier", "union_specifier", "enum_specifier"}:
            _add_aggregate(child, prefix, api)
        elif kind == "type_definition":
            _typedef_declaration(child, prefix, api)
        elif kind in {"preproc_ifdef", "preproc_if", "preproc_else", "preproc_elif"}:
            _declarations(child, prefix, api)


def _declarations_one(
    node: Node, prefix: str, api: dict[str, str], template_params: str = ""
) -> None:
    if _is_static(node):
        return
    if node.type in {"struct_specifier", "class_specifier", "union_specifier", "enum_specifier"}:
        _add_aggregate(node, prefix, api, template_params)
        return
    declarator = _field(node, "declarator")
    if declarator is not None and declarator.type == "function_declarator":
        _add_function(node, declarator, prefix, api, template_params)
        return
    for child in node.named_children:
        if child.type in {"struct_specifier", "class_specifier", "union_specifier", "enum_specifier"}:
            _add_aggregate(child, prefix, api, template_params)


def _skip_quoted(text: str, start: int, quote: str) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return len(text)


def _skip_comment(text: str, start: int) -> int:
    if text.startswith("//", start):
        newline = text.find("\n", start)
        return len(text) if newline < 0 else newline
    end = text.find("*/", start + 2)
    return len(text) if end < 0 else end + 2


def _balanced_end(text: str, start: int) -> int:
    depth = 0
    i = start
    while i < len(text):
        if text.startswith("//", i) or text.startswith("/*", i):
            i = _skip_comment(text, i)
            continue
        char = text[i]
        if char in {'"', "'"}:
            i = _skip_quoted(text, i, char)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _declaration_end(text: str, start: int) -> int:
    depth = 0
    seen_brace = False
    i = start
    while i < len(text):
        if text.startswith("//", i) or text.startswith("/*", i):
            i = _skip_comment(text, i)
            continue
        char = text[i]
        if char in {'"', "'"}:
            i = _skip_quoted(text, i, char)
            continue
        if char == "{":
            depth += 1
            seen_brace = True
        elif char == "}":
            depth -= 1
            if depth == 0 and seen_brace:
                end = i + 1
                while end < len(text) and text[end] in " \t\r\n":
                    end += 1
                return end + 1 if end < len(text) and text[end] == ";" else i + 1
        elif char == ";" and depth == 0:
            return i + 1
        i += 1
    return len(text)


def _blank_range(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] != "\n":
            chars[index] = " "


def _module_parse_text(path: str, text: str) -> str:
    if Path(path).suffix not in _MODULE_EXT or _MODULE_RE.search(text) is None:
        return text
    chars = list(text)
    for pattern in (_MODULE_LINE_RE, _MODULE_EMPTY_RE, _MODULE_PRIVATE_RE, _IMPORT_LINE_RE):
        for match in pattern.finditer(text):
            _blank_range(chars, match.start(), match.end())
    position = 0
    while True:
        match = _EXPORT_RE.search(text, position)
        if match is None:
            break
        start = match.end()
        while start < len(text) and text[start] in " \t":
            start += 1
        if text.startswith("module", start) or text.startswith("import", start):
            position = start + 6
            continue
        if start < len(text) and text[start] == "{":
            end = _balanced_end(text, start)
            _blank_range(chars, match.start(), start + 1)
            _blank_range(chars, end - 1, end)
            position = end
        else:
            _blank_range(chars, match.start(), match.end())
            position = match.end()
    return "".join(chars)


def _module_export_source(tree: CFile) -> str | None:
    if Path(tree.path).suffix not in _MODULE_EXT:
        return None
    text = tree.tree.root_node.text.decode("utf-8", "replace")
    if _MODULE_RE.search(text) is None:
        return None
    parts: list[str] = []
    position = 0
    while True:
        match = _EXPORT_RE.search(text, position)
        if match is None:
            break
        start = match.end()
        while start < len(text) and text[start] in " \t":
            start += 1
        if text.startswith("module", start) or text.startswith("import", start):
            position = start + 6
            continue
        if start < len(text) and text[start] == "{":
            end = _balanced_end(text, start)
            parts.append(text[start + 1 : end - 1])
            position = end
        else:
            end = _declaration_end(text, start)
            parts.append(text[start:end])
            position = end
    return "\n".join(parts)


def _module_public_api(tree: CFile) -> dict[str, str] | None:
    source = _module_export_source(tree)
    if source is None:
        return None
    text = tree.tree.root_node.text.decode("utf-8", "replace")
    parsed = _parse("cpp", tree.path, source)
    api: dict[str, str] = {}
    identity = _MODULE_IDENTITY_RE.search(text)
    if identity is not None:
        name = identity.group(1)
        api["module:" + name] = "module:" + name
    for match in _EXPORT_IMPORT_RE.finditer(text):
        target = match.group(1).strip()
        api["export-import:" + target] = "export-import:" + target
    _declarations(parsed.tree.root_node, "", api)
    return api


def abi_surface(path: str, text: str | None) -> dict[str, str]:
    if Path(path).suffix not in _HEADERS:
        return {}
    parsed = parse_text(path, text)
    if parsed is None:
        return {}
    api: dict[str, str] = {}
    _abi_declarations(parsed.tree.root_node, "", api)
    return api


_LAYOUT_ATTRIBUTE_RE = re.compile(
    r"\[\[[^\]]*\]\]|__attribute__\s*\(\([^)]*\)\)|__declspec\s*\([^)]*\)"
)


def _record_header(node: Node) -> str:
    text = _COMMENT_RE.sub("", _text(node).split("{", 1)[0])

    def keep_layout(match: re.Match[str]) -> str:
        value = match.group(0)
        return value if any(token in value for token in ("packed", "align", "aligned")) else ""

    text = _LAYOUT_ATTRIBUTE_RE.sub(keep_layout, text)
    text = re.sub(r"\bfinal\b", "", text)
    return _norm(text)


def _abi_field(child: Node) -> str:
    parts: list[str] = []
    type_node = _field(child, "type")
    if type_node is not None:
        parts.append(_semantic_text(_text(type_node)))
    for nested in child.named_children:
        if nested is type_node:
            continue
        if nested.type in {
            "field_identifier",
            "array_declarator",
            "pointer_declarator",
            "reference_declarator",
            "function_declarator",
            "qualified_identifier",
        }:
            parts.append(_semantic_text(_text(nested)))
        elif nested.type == "bitfield_clause":
            parts.append(_semantic_text(_text(nested)))
        elif nested.type in {"type_qualifier", "attribute_declaration", "attribute_specifier"}:
            text = _semantic_text(_text(nested))
            if any(token in text for token in ("alignas", "packed", "aligned")):
                parts.append(text)
    return "field:" + "|".join(parts)


def _abi_record(
    node: Node,
    prefix: str,
    api: dict[str, str],
    template_params: str = "",
    name_override: str = "",
) -> None:
    name = name_override or _name(_field(node, "name"))
    if not name:
        return
    parts = [node.type, _record_header(node)]
    if template_params:
        parts.append(template_params)
    body = _field(node, "body")
    if body is not None:
        for child in body.named_children:
            if child.type == "field_declaration":
                declarator = _field(child, "declarator")
                if declarator is not None and declarator.type == "function_declarator":
                    if "virtual" in _semantic_text(_text(child)):
                        parts.append(
                            "virtual:" + _semantic_text(_function_signature(child, declarator))
                        )
                    continue
                if _is_static(child):
                    continue
                parts.append(_abi_field(child))
            elif child.type in {"declaration", "function_definition"}:
                declarator = _field(child, "declarator")
                if (
                    declarator is not None
                    and declarator.type == "function_declarator"
                    and "virtual" in _semantic_text(_text(child))
                ):
                    parts.append(
                        "virtual:" + _semantic_text(_function_signature(child, declarator))
                    )
    api[prefix + name] = "|".join(parts)


def _abi_enum(
    node: Node,
    prefix: str,
    api: dict[str, str],
    template_params: str = "",
    name_override: str = "",
) -> None:
    name = name_override or _name(_field(node, "name"))
    if name:
        value = "enum:" + _record_header(node)
        if template_params:
            value += ":" + template_params
        api[prefix + name] = value


def _abi_typedef(node: Node, prefix: str, api: dict[str, str]) -> None:
    nested: Node | None = None
    alias = ""
    for child in node.named_children:
        if child.type in {
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
        } and nested is None:
            nested = child
        elif child.type in {"type_identifier", "identifier"}:
            alias = _name(child)
    if nested is None or _field(nested, "body") is None:
        return
    name = _name(_field(nested, "name")) or alias
    if not name:
        return
    if nested.type == "enum_specifier":
        _abi_enum(nested, prefix, api, name_override=name)
    else:
        _abi_record(nested, prefix, api, name_override=name)


def _abi_declarations(container: Node, prefix: str, api: dict[str, str]) -> None:
    for child in container.named_children:
        kind = child.type
        if kind == "namespace_definition":
            name = _name(_field(child, "name"))
            body = _field(child, "body")
            if body is not None and name:
                _abi_declarations(body, prefix + name + "::", api)
        elif kind == "linkage_specification":
            body = _field(child, "body")
            if body is not None:
                _abi_declarations(body, prefix, api)
        elif kind == "template_declaration":
            template_params = ""
            for nested in child.named_children:
                if nested.type == "template_parameter_list":
                    template_params = _semantic_text(_text(nested))
                elif nested.type in {"struct_specifier", "class_specifier", "union_specifier"}:
                    _abi_record(nested, prefix, api, template_params)
                elif nested.type == "enum_specifier":
                    _abi_enum(nested, prefix, api, template_params)
        elif kind in {"struct_specifier", "class_specifier", "union_specifier"}:
            _abi_record(child, prefix, api)
        elif kind == "enum_specifier":
            _abi_enum(child, prefix, api)
        elif kind == "type_definition":
            _abi_typedef(child, prefix, api)
        elif kind in {"preproc_ifdef", "preproc_if", "preproc_else", "preproc_elif"}:
            _abi_declarations(child, prefix, api)


def public_api(tree: CFile | None) -> dict[str, str]:
    if tree is None or Path(tree.path).suffix not in _HEADERS:
        return {}
    module_api = _module_public_api(tree)
    if module_api is not None:
        return module_api
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
