"""JavaScript and TypeScript analysis backed by pinned tree-sitter grammars.

The parser is a declared dependency with exact pins. It runs in-process, offline,
and needs no Node toolchain; the tree is used only for deterministic diff
analysis, never for type checking or execution.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Tree

from .config import Config
from .model import ImportRef, TestInfo

NAME = "javascript"
EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")

STDLIB = frozenset(
    {
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "diagnostics_channel",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "sea",
        "sqlite",
        "stream",
        "string_decoder",
        "sys",
        "test",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
    }
)

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

_FUNCTION_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
    }
)
_CLASS_TYPES = frozenset({"class_declaration", "class_expression"})
_BRANCH_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "catch_clause",
        "ternary_expression",
    }
)
_LOGICAL = frozenset({"&&", "||", "??"})
_TEST_VERBS = {
    "describe": "describe",
    "context": "describe",
    "suite": "describe",
    "xdescribe": "describe",
    "fdescribe": "describe",
    "test": "test",
    "it": "test",
    "specify": "test",
    "xit": "test",
    "fit": "test",
}
_SKIP_FLAGS = frozenset({"skip", "todo", "only", "failing"})

_LANG_JS = Language(tree_sitter_javascript.language())
_LANG_TS = Language(tree_sitter_typescript.language_typescript())
_LANG_TSX = Language(tree_sitter_typescript.language_tsx())
_PARSERS = {
    "js": Parser(_LANG_JS),
    "ts": Parser(_LANG_TS),
    "tsx": Parser(_LANG_TSX),
}


@dataclass(frozen=True)
class JsFile:
    tree: Tree
    path: str


def _grammar(path: str) -> str:
    if path.endswith(".tsx"):
        return "tsx"
    if path.endswith((".ts", ".mts", ".cts")):
        return "ts"
    return "js"


@lru_cache(maxsize=4096)
def _parse_cached(grammar: str, text: str) -> Tree:
    return _PARSERS[grammar].parse(text.encode("utf-8"))


def parse_text(path: str, text: str | None) -> JsFile | None:
    if text is None:
        return None
    tree = _parse_cached(_grammar(path), text)
    return JsFile(tree, path)


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


def _string_value(node: Node) -> str:
    raw = _text(node)
    if len(raw) >= 2 and raw[0] in "'\"`" and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def _signature(node: Node) -> str:
    parts: list[str] = []
    type_parameters = _field(node, "type_parameters")
    if type_parameters is not None:
        parts.append(_text(type_parameters))
    parameters = _field(node, "parameters")
    raw = _text(parameters) if parameters is not None else "()"
    if not raw.startswith("("):
        raw = f"({raw})"
    parts.append(raw)
    returns = _field(node, "return_type")
    if returns is not None:
        parts.append(_text(returns))
    return _norm("".join(parts))


def _heritage(node: Node) -> str:
    parts = [
        _text(child)
        for child in node.named_children
        if child.type in {"class_heritage", "implements_clause"}
    ]
    return _norm(" ".join(parts)) or "class"


def _is_hidden(member: Node) -> bool:
    name = _field(member, "name")
    if name is not None and name.type == "private_property_identifier":
        return True
    return any(
        child.type == "accessibility_modifier" and _text(child) in {"private", "protected"}
        for child in member.children
    )


def _member_modifiers(member: Node) -> str:
    return "".join(
        child.type
        for child in member.children
        if child.type in {"static", "get", "set", "async", "abstract", "override"}
        or child.type == "*"
    )


def _method_api(member: Node, owner: str, api: dict[str, str]) -> None:
    if _is_hidden(member):
        return
    name = _field(member, "name")
    if name is None:
        return
    api[f"{owner}.{_text(name)}"] = _member_modifiers(member) + _signature(member)


def _field_api(member: Node, owner: str, api: dict[str, str]) -> None:
    if _is_hidden(member):
        return
    name = _field(member, "name")
    if name is None:
        return
    type_node = _field(member, "type")
    value = _field(member, "value")
    parts = [_text(type_node) if type_node is not None else ""]
    parts.append(_text(value) if value is not None else "")
    api[f"{owner}.{_text(name)}"] = "field:" + _norm("".join(parts))


def _class_api(node: Node, key: str, api: dict[str, str], set_class: bool = True) -> None:
    if set_class:
        api[key] = "class:" + _heritage(node)
    body = _field(node, "body")
    if body is None:
        return
    for member in body.named_children:
        if member.type in {"method_definition", "abstract_method_signature", "method_signature"}:
            _method_api(member, key, api)
        elif member.type == "public_field_definition":
            _field_api(member, key, api)


def _pattern_names(node: Node | None) -> list[str]:
    if node is None:
        return []
    if node.type in {"identifier", "shorthand_property_identifier"}:
        return [_text(node)]
    if node.type in {"object_pattern", "array_pattern"}:
        names: list[str] = []
        for child in node.named_children:
            names.extend(_pattern_names(child))
        return names
    if node.type == "pair":
        return _pattern_names(_field(node, "key"))
    if node.type in {"rest_pattern", "assignment_pattern"}:
        return _pattern_names(node.named_children[0] if node.named_children else None)
    return []


def _value_signature(value: Node | None, type_node: Node | None) -> str:
    if value is not None and value.type in _FUNCTION_TYPES:
        return "fn:" + _signature(value)
    if value is not None and value.type in _CLASS_TYPES:
        return "class:" + _heritage(value)
    if type_node is not None:
        return "const:" + _norm(_text(type_node).lstrip(":"))
    return "const"


def _lexical_api(declaration: Node, api: dict[str, str]) -> None:
    for declarator in declaration.named_children:
        if declarator.type != "variable_declarator":
            continue
        names = _pattern_names(_field(declarator, "name"))
        signature = _value_signature(_field(declarator, "value"), _field(declarator, "type"))
        for name in names:
            api[name] = signature


def _function_decl_api(declaration: Node, api: dict[str, str], default: bool) -> None:
    name = _field(declaration, "name")
    if name is None and not default:
        return
    api["default" if default else _text(name)] = "fn:" + _signature(declaration)


def _class_decl_api(declaration: Node, api: dict[str, str], default: bool) -> None:
    name = _field(declaration, "name")
    key = _text(name) if name is not None else ("default" if default else "")
    if not key:
        return
    if default:
        api["default"] = "class:" + _heritage(declaration)
    _class_api(declaration, key, api, set_class=not default)


def _named_decl_api(declaration: Node, api: dict[str, str]) -> None:
    name = _field(declaration, "name")
    if name is not None:
        api[_text(name)] = f"{declaration.type}:{_norm(_text(declaration))}"


def _declaration_api(declaration: Node, api: dict[str, str], default: bool) -> None:
    kind = declaration.type
    if kind in {"function_declaration", "generator_function_declaration"}:
        _function_decl_api(declaration, api, default)
    elif kind in _CLASS_TYPES:
        _class_decl_api(declaration, api, default)
    elif kind in {"function_signature", "method_signature"}:
        name = _field(declaration, "name")
        if name is not None:
            api[_text(name)] = "fn:" + _signature(declaration)
    elif kind in {"interface_declaration", "type_alias_declaration", "enum_declaration"}:
        _named_decl_api(declaration, api)
    elif kind == "lexical_declaration":
        _lexical_api(declaration, api)
    elif kind == "ambient_declaration":
        for child in declaration.named_children:
            _declaration_api(child, api, default)


def _export_clause(clause: Node, api: dict[str, str]) -> None:
    for specifier in clause.named_children:
        if specifier.type != "export_specifier":
            continue
        name = _field(specifier, "name")
        alias = _field(specifier, "alias")
        exported = _text(alias) if alias is not None else _text(name)
        if exported:
            api[exported] = "exported"


def _object_api(node: Node, api: dict[str, str]) -> None:
    for child in node.named_children:
        if child.type == "shorthand_property_identifier":
            api[_text(child)] = "cjs"
        elif child.type == "pair":
            key = _field(child, "key")
            if key is not None:
                api[_text(key).strip("'\"")] = "cjs"
        elif child.type == "method_definition":
            name = _field(child, "name")
            if name is not None:
                api[_text(name)] = "cjs"


def _cjs_api(statement: Node, api: dict[str, str]) -> None:
    expression = next(
        (child for child in statement.named_children if child.type == "assignment_expression"),
        None,
    )
    if expression is None:
        return
    left = _field(expression, "left")
    right = _field(expression, "right")
    if left is None or right is None:
        return
    path = _norm(_text(left))
    if path == "module.exports":
        if right.type == "object":
            _object_api(right, api)
        else:
            api["default"] = "cjs"
    elif path.startswith("module.exports."):
        api[path[len("module.exports.") :]] = "cjs"
    elif path.startswith("exports."):
        api[path[len("exports.") :]] = "cjs"


def _export_api(node: Node, api: dict[str, str]) -> None:
    declaration = _field(node, "declaration")
    default = any(child.type == "default" for child in node.children)
    if declaration is not None:
        _declaration_api(declaration, api, default)
        return
    value = _field(node, "value")
    if value is not None:
        api["default"] = "value:" + _norm(_text(value))
        return
    clause = next((child for child in node.named_children if child.type == "export_clause"), None)
    if clause is not None:
        _export_clause(clause, api)
        return
    namespace = next(
        (child for child in node.named_children if child.type == "namespace_export"),
        None,
    )
    source = _field(node, "source")
    if namespace is not None:
        api["*as:" + _norm(_text(namespace))] = _norm(_text(source))
        return
    if source is not None:
        api["*from:" + _string_value(source)] = "exported"
        return
    if any(child.type == "=" for child in node.children):
        api["export="] = "value:" + _norm(_text(node))


def public_api(tree: JsFile | None) -> dict[str, str]:
    if tree is None:
        return {}
    api: dict[str, str] = {}
    for node in tree.tree.root_node.named_children:
        if node.type == "export_statement":
            _export_api(node, api)
        elif node.type == "expression_statement":
            _cjs_api(node, api)
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
        if child.type in _FUNCTION_TYPES or child.type in _CLASS_TYPES:
            continue
        total += _branches(child)
    return total


def _record(node: Node, key: str, scores: dict[str, int]) -> None:
    body = _field(node, "body")
    if body is None or not key:
        return
    score = 1 + _branches(body)
    scores[key] = max(scores.get(key, 0), score)


def _binding_name(node: Node | None) -> str:
    return _text(node) if node is not None and node.type == "identifier" else ""


def _collect_class(
    node: Node, owner: str | None, scores: dict[str, int], handled: set[int]
) -> None:
    name = _field(node, "name")
    class_owner = _text(name) if name is not None else owner
    for child in node.named_children:
        _collect_functions(child, class_owner, scores, handled)


def _collect_pair(node: Node, scores: dict[str, int], handled: set[int]) -> None:
    key = _field(node, "key")
    value = _field(node, "value")
    if key is not None and value is not None and value.type in _FUNCTION_TYPES:
        _record(value, _text(key).strip("'\""), scores)
        handled.add(value.id)


def _collect_function(
    node: Node, owner: str | None, scores: dict[str, int], handled: set[int]
) -> None:
    if node.id not in handled:
        name = _field(node, "name")
        if name is not None:
            key = f"{owner}.{_text(name)}" if owner else _text(name)
            _record(node, key, scores)
    for child in node.named_children:
        _collect_functions(child, None, scores, handled)


def _collect_functions(
    node: Node,
    owner: str | None,
    scores: dict[str, int],
    handled: set[int],
) -> None:
    kind = node.type
    if kind in _CLASS_TYPES:
        _collect_class(node, owner, scores, handled)
        return
    if kind == "variable_declarator":
        _collect_declarator(node, scores, handled)
        for child in node.named_children:
            _collect_functions(child, None, scores, handled)
        return
    if kind == "pair":
        _collect_pair(node, scores, handled)
        for child in node.named_children:
            _collect_functions(child, None, scores, handled)
        return
    if kind in _FUNCTION_TYPES:
        _collect_function(node, owner, scores, handled)
        return
    for child in node.named_children:
        _collect_functions(child, owner, scores, handled)


def _collect_declarator(node: Node, scores: dict[str, int], handled: set[int]) -> None:
    value = _field(node, "value")
    binding = _binding_name(_field(node, "name"))
    if value is None or not binding:
        return
    if value.type in _FUNCTION_TYPES:
        _record(value, binding, scores)
        handled.add(value.id)
    elif value.type in _CLASS_TYPES:
        _collect_functions(value, binding, scores, handled)


def complexity(tree: JsFile | None) -> dict[str, int]:
    if tree is None:
        return {}
    scores: dict[str, int] = {}
    _collect_functions(tree.tree.root_node, None, scores, set())
    return scores


def _callee_path(node: Node | None) -> list[str]:
    if node is None:
        return []
    if node.type in {"identifier", "private_property_identifier", "property_identifier"}:
        return [_text(node)]
    if node.type == "member_expression":
        parts = _callee_path(_field(node, "object"))
        property_node = _field(node, "property")
        if property_node is not None:
            parts.append(_text(property_node))
        return parts
    return []


def _title(arguments: Node) -> str:
    for child in arguments.named_children:
        if child.type in {"string", "template_string"}:
            return _string_value(child)
    return ""


def _callback(arguments: Node) -> Node | None:
    for child in arguments.named_children:
        if child.type in _FUNCTION_TYPES and child.type != "method_definition":
            return child
    return None


def _test_call(node: Node) -> tuple[str, str, bool, Node | None, int] | None:
    function = _field(node, "function")
    path = _callee_path(function)
    case_count = 0
    if function is not None and function.type == "call_expression":
        inner = _field(function, "function")
        inner_path = _callee_path(inner)
        if inner_path and inner_path[-1] == "each":
            path = inner_path
            case_count = _each_case_count(function)
    if not path:
        return None
    base = path[0]
    verb = _TEST_VERBS.get(base)
    if verb is None:
        return None
    skip = base in {"xdescribe", "fdescribe", "xit", "fit"} or any(
        part in _SKIP_FLAGS for part in path[1:]
    )
    arguments = _field(node, "arguments")
    if arguments is None:
        return None
    return verb, _title(arguments), skip, _callback(arguments), case_count


def _template_case_count(node: Node) -> int:
    return sum(1 for line in _text(node).splitlines() if "${" in line)


def _each_case_count(call: Node) -> int:
    arguments = _field(call, "arguments")
    if arguments is not None:
        if arguments.type == "template_string":
            return _template_case_count(arguments)
        for child in arguments.named_children:
            if child.type == "array":
                return len(child.named_children)
        return 0
    for child in call.named_children:
        if child.type == "template_string":
            return _template_case_count(child)
    return 0


def _collect_tests(
    node: Node,
    prefix: str,
    inherited_skip: bool,
    functions: set[str],
    skips: set[str],
    counts: dict[str, int],
    cases: dict[str, int],
) -> None:
    for child in node.named_children:
        if child.type != "call_expression":
            _collect_tests(child, prefix, inherited_skip, functions, skips, counts, cases)
            continue
        call = _test_call(child)
        if call is None:
            _collect_tests(child, prefix, inherited_skip, functions, skips, counts, cases)
            continue
        verb, title, skip, callback, case_count = call
        if verb == "describe":
            next_prefix = f"{prefix}{title} > " if title else prefix
            body = _field(callback, "body")
            if body is not None:
                _collect_tests(
                    body,
                    next_prefix,
                    inherited_skip or skip,
                    functions,
                    skips,
                    counts,
                    cases,
                )
            continue
        if not title:
            continue
        name = f"{prefix}{title}"
        functions.add(name)
        counts[name] = _count_assertions(callback) if callback is not None else 0
        if case_count:
            cases[name] = case_count
        if inherited_skip or skip:
            skips.add(name)


def _count_assertions(node: Node) -> int:
    total = 0
    for child in node.named_children:
        if child.type == "call_expression":
            path = _callee_path(_field(child, "function"))
            if "expect" in path or "assert" in path:
                total += 1
        total += _count_assertions(child)
    return total


def test_info(tree: JsFile | None) -> TestInfo:
    if tree is None:
        return TestInfo(frozenset(), 0, frozenset())
    functions: set[str] = set()
    skips: set[str] = set()
    counts: dict[str, int] = {}
    cases: dict[str, int] = {}
    _collect_tests(tree.tree.root_node, "", False, functions, skips, counts, cases)
    assertions = _count_assertions(tree.tree.root_node)
    return TestInfo(frozenset(functions), assertions, frozenset(skips), counts, cases)


def _first_string(arguments: Node | None) -> str:
    if arguments is None:
        return ""
    for child in arguments.named_children:
        if child.type == "string":
            return _string_value(child)
    return ""


def _collect_import_specs(node: Node, specs: list[tuple[str, int]]) -> None:
    if node.type in {"import_statement", "export_statement"}:
        source = _field(node, "source")
        if source is not None:
            specs.append((_string_value(source), _line(node)))
    if node.type == "call_expression":
        callee = _field(node, "function")
        if callee is not None and _text(callee) in {"require", "import"}:
            literal = _first_string(_field(node, "arguments"))
            if literal:
                specs.append((literal, _line(node)))
    for child in node.named_children:
        _collect_import_specs(child, specs)


def _module_root(specifier: str) -> str:
    value = specifier.strip()
    if not value:
        return ""
    if value.startswith((".", "/", "@/", "~/", "#")):
        return ""
    if value.startswith("node:"):
        return value
    if value.startswith("@"):
        parts = value.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else value
    return value.split("/")[0]


def _imports(tree: JsFile, resolver=None) -> list[ImportRef]:
    specs: list[tuple[str, int]] = []
    _collect_import_specs(tree.tree.root_node, specs)
    refs: list[ImportRef] = []
    seen: set[str] = set()
    for specifier, line in specs:
        module = resolver(specifier) if resolver is not None else None
        if module is None:
            module = _module_root(specifier)
        if not module or module in seen:
            continue
        seen.add(module)
        refs.append(ImportRef(module=module, level=0, line=line))
    return refs


def imports(tree: JsFile) -> list[ImportRef]:
    return _imports(tree)


def is_stdlib(module: str) -> bool:
    return module.startswith("node:") or module in STDLIB


def _repo_files(root: Path) -> tuple[list[str], list[str], list[str]]:
    manifests: list[str] = []
    sources: list[str] = []
    configs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        relative = os.path.relpath(dirpath, root)
        relative = "" if relative == "." else Path(relative).as_posix()
        for name in sorted(filenames):
            rel = f"{relative}/{name}" if relative else name
            if name == "package.json":
                manifests.append(rel)
            elif name in {"tsconfig.json", "jsconfig.json"}:
                configs.append(rel)
            elif name.endswith(EXTENSIONS):
                sources.append(rel)
    return manifests, sources, configs


def _read_package(root: Path, rel: str) -> tuple[str, dict[str, tuple[str, int]]] | None:
    try:
        text = (root / rel).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    deps: dict[str, tuple[str, int]] = {}
    for table in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = data.get(table)
        if isinstance(values, dict):
            for dependency in values:
                deps.setdefault(str(dependency), (rel, 1))
    return name, deps


@dataclass(frozen=True)
class _TsConfig:
    base_url: str
    paths: tuple[tuple[str, tuple[str, ...]], ...]


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments outside strings."""
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    """Remove commas before } or ] outside strings."""
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == ",":
            look = index + 1
            while look < len(text) and text[look].isspace():
                look += 1
            if look < len(text) and text[look] in "}]":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def _strip_jsonc(text: str) -> str:
    """Strip comments and trailing commas outside strings."""
    return _strip_trailing_commas(_strip_comments(text))


def _load_jsonc(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(_strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _extends_path(root: Path, current: str, value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith((".", "/")):
        return None
    base = (root / current).parent
    candidate = (base / value).resolve()
    if not candidate.is_relative_to(root.resolve()):
        return None
    choices = [candidate]
    if candidate.suffix == "":
        choices.extend([Path(str(candidate) + ".json"), candidate / "tsconfig.json"])
    for choice in choices:
        if choice.is_file() and choice.is_relative_to(root.resolve()):
            return choice.relative_to(root.resolve()).as_posix()
    return None


def _inherited_tsconfig(
    root: Path,
    rel: str,
    data: dict[str, object],
    seen: set[str],
) -> _TsConfig | None:
    inherited: _TsConfig | None = None
    extends = data.get("extends")
    values = extends if isinstance(extends, list) else [extends]
    for value in values:
        parent_rel = _extends_path(root, rel, value)
        if parent_rel is None:
            continue
        parent = _read_tsconfig(root, parent_rel, seen)
        if parent is not None:
            inherited = parent
    return inherited


def _path_mapping(
    compiler: dict[str, object],
    inherited: _TsConfig | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    paths = inherited.paths if inherited is not None else ()
    raw_paths = compiler.get("paths")
    if not isinstance(raw_paths, dict):
        return paths
    parsed: list[tuple[str, tuple[str, ...]]] = []
    for pattern, targets in raw_paths.items():
        if not isinstance(targets, list):
            continue
        values = tuple(target for target in targets if isinstance(target, str) and target)
        if values:
            parsed.append((str(pattern), values))
    return tuple(parsed)


def _read_tsconfig(root: Path, rel: str, seen: set[str] | None = None) -> _TsConfig | None:
    seen = set() if seen is None else seen
    if rel in seen:
        return None
    seen.add(rel)
    data = _load_jsonc(root / rel)
    if data is None:
        return None

    inherited = _inherited_tsconfig(root, rel, data, seen)
    config_dir = posixpath.dirname(rel) or "."
    compiler = data.get("compilerOptions")
    compiler = compiler if isinstance(compiler, dict) else {}
    base_url = inherited.base_url if inherited is not None else config_dir
    raw_base = compiler.get("baseUrl")
    if isinstance(raw_base, str) and raw_base:
        base_url = posixpath.normpath(posixpath.join(config_dir, raw_base))
    return _TsConfig(base_url=base_url, paths=_path_mapping(compiler, inherited))



class RepoIndex:
    """npm package names, declared dependencies, and parsed sources."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.packages: dict[str, str] = {}
        self.deps: dict[str, tuple[str, int]] = {}
        self.files: dict[str, JsFile] = {}
        self.tsconfigs: dict[str, _TsConfig] = {}
        self._scan()

    def _scan(self) -> None:
        manifests, sources, configs = _repo_files(self.root)
        for rel in manifests:
            facts = _read_package(self.root, rel)
            if facts is None:
                continue
            name, deps = facts
            self.packages[posixpath.dirname(rel)] = name
            for dependency, site in deps.items():
                self.deps.setdefault(dependency, site)
        for rel in configs:
            config = _read_tsconfig(self.root, rel)
            if config is not None:
                self.tsconfigs[posixpath.dirname(rel)] = config
        for rel in sources:
            try:
                text = (self.root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            tree = parse_text(rel, text)
            if tree is not None:
                self.files[rel] = tree

    def package_of(self, rel: str) -> str:
        directory = posixpath.dirname(rel)
        while True:
            name = self.packages.get(directory)
            if name:
                return name
            if not directory:
                return ""
            directory = posixpath.dirname(directory)

    def _nearest_tsconfig(self, rel: str) -> _TsConfig | None:
        directory = posixpath.dirname(rel)
        while True:
            config = self.tsconfigs.get(directory)
            if config is not None:
                return config
            if not directory:
                return None
            directory = posixpath.dirname(directory)

    def _resolve_alias(self, rel: str, specifier: str) -> str | None:
        value = specifier.strip()
        if not value or value.startswith((".", "/", "#")) or value.startswith("node:"):
            return None
        config = self._nearest_tsconfig(rel)
        if config is None or not config.paths:
            return None

        matched = _match_alias(config, value)
        if matched is None:
            return None
        targets, wildcard = matched
        for target in targets:
            resolved = _alias_target(config, target, wildcard)
            if resolved is None:
                continue
            package = self.package_of(resolved)
            return package if package else ""
        return None


@lru_cache(maxsize=4)
def _cached_index(root: str) -> RepoIndex:
    return RepoIndex(Path(root))


def _clear_index_cache() -> None:
    _cached_index.cache_clear()


def _index_for(root: Path) -> RepoIndex:
    if root.name.startswith("tree-aed-"):
        return _cached_index(str(root))
    return RepoIndex(root)


def _match_alias(
    config: _TsConfig,
    value: str,
) -> tuple[tuple[str, ...], str] | None:
    matches: list[tuple[int, tuple[str, ...], str]] = []
    for pattern, targets in config.paths:
        if "*" not in pattern:
            if value == pattern:
                matches.append((len(pattern), targets, ""))
            continue
        prefix, suffix = pattern.split("*", 1)
        if not value.startswith(prefix) or not value.endswith(suffix):
            continue
        end = len(value) - len(suffix) if suffix else len(value)
        matches.append((len(prefix), targets, value[len(prefix) : end]))
    if not matches:
        return None
    _, targets, wildcard = max(matches, key=lambda item: item[0])
    return targets, wildcard


def _alias_target(config: _TsConfig, target: str, wildcard: str) -> str | None:
    resolved = posixpath.normpath(
        posixpath.join(config.base_url, target.replace("*", wildcard))
    )
    if resolved == ".." or resolved.startswith(("../", "/")):
        return None
    return resolved


def package_edges(root: Path, config: Config) -> tuple[set[tuple[str, str]], set[str]]:
    index = _index_for(root)
    packages = set(index.packages.values())
    edges: set[tuple[str, str]] = set()
    for rel, tree in index.files.items():
        if config.is_excluded(rel) or config.is_test(rel):
            continue
        package = index.package_of(rel)
        if not package:
            continue
        for ref in _imports(tree, lambda spec: index._resolve_alias(rel, spec)):
            if ref.module in packages and ref.module != package:
                edges.add((package, ref.module))
    return edges, packages


def project_imports(root: Path, config: Config) -> dict[str, tuple[str, int]]:
    index = _index_for(root)
    found: dict[str, tuple[str, int]] = dict(index.deps)
    for rel, tree in index.files.items():
        if config.is_excluded(rel) or config.is_test(rel):
            continue
        for ref in _imports(tree, lambda spec: index._resolve_alias(rel, spec)):
            if ref.level != 0 or not ref.module:
                continue
            found.setdefault(ref.module, (rel, ref.line))
    return found


def declared_dependencies(root: Path, _config: Config) -> dict[str, tuple[str, int]]:
    return _index_for(root).deps


def local_packages(root: Path, _config: Config) -> set[str]:
    return set(_index_for(root).packages.values())
