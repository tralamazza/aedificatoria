"""Deterministic Rust source analysis: imports, public API, complexity, tests.

Rust support uses a small lexer and brace matching over a token stream, so it
stays dependency-free and reproducible. Comments, strings, raw strings, char
literals, and lifetimes are tokenised away from structural punctuation, which
keeps braces and item keywords inside them from confusing the analysis.
"""

from __future__ import annotations

import os
import posixpath
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .model import ImportRef, TestInfo

NAME = "rust"
EXTENSIONS = (".rs",)

STDLIB = frozenset({"std", "core", "alloc", "proc_macro", "test"})

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "target",
    "dist",
    "build",
}

_ITEMS = frozenset(
    {"fn", "struct", "enum", "union", "trait", "type", "const", "static", "mod", "impl", "use"}
)
_MODIFIERS = frozenset({"unsafe", "async", "default"})

_PUNCT = frozenset(
    {
        "::",
        "->",
        "=>",
        "&&",
        "||",
        "..=",
        "..",
        "==",
        "!=",
        "<=",
        ">=",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "&=",
        "|=",
        "^=",
        "<<=",
        ">>=",
        "<<",
        ">>",
    }
)

_TEST_ATTRS = frozenset({"test", "rstest", "test_case", "quickcheck"})


@dataclass(frozen=True)
class Token:
    kind: str  # "ident", "lit", or "punct"
    value: str
    line: int


@dataclass(frozen=True)
class RustFile:
    tokens: tuple[Token, ...]
    api: dict[str, str]
    complexity: dict[str, int]
    tests: frozenset[str]
    assertions: int
    skips: frozenset[str]


@dataclass(frozen=True)
class _Head:
    keyword: int
    attrs: tuple[str, ...]
    public: bool


def _skip_line_comment(text: str, index: int) -> int:
    newline = text.find("\n", index)
    return len(text) if newline < 0 else newline


def _skip_block_comment(text: str, index: int, line: int) -> tuple[int, int]:
    depth = 1
    index += 2
    while index < len(text) and depth:
        if text.startswith("/*", index):
            depth += 1
            index += 2
        elif text.startswith("*/", index):
            depth -= 1
            index += 2
        else:
            if text[index] == "\n":
                line += 1
            index += 1
    return index, line


def _match_raw_string(text: str, index: int, line: int) -> tuple[Token, int, int] | None:
    match = re.match(r'(?:b|c)?r(#*)"', text[index:])
    if not match:
        return None
    hashes = match.group(1)
    start_line = line
    index += match.end()
    terminator = '"' + hashes
    end = text.find(terminator, index)
    if end < 0:
        segment = text[index:]
        index = len(text)
    else:
        segment = text[index:end]
        index = end + len(terminator)
    return Token("lit", '"…"', start_line), index, line + segment.count("\n")


def _match_string(text: str, index: int, line: int) -> tuple[Token, int, int] | None:
    byte_prefix = 0
    if text[index] in "bc" and index + 1 < len(text) and text[index + 1] == '"':
        byte_prefix = 1
    if text[index] != '"' and not byte_prefix:
        return None
    start_line = line
    index += byte_prefix + 1
    while index < len(text):
        current = text[index]
        if current == "\\":
            if index + 1 < len(text) and text[index + 1] == "\n":
                line += 1
            index += 2
            continue
        if current == '"':
            index += 1
            break
        if current == "\n":
            line += 1
        index += 1
    return Token("lit", '"…"', start_line), index, line


def _match_char(text: str, index: int, line: int) -> tuple[Token, int] | None:
    if text.startswith("b'", index):
        match = re.match(r"b'(?:\\.|[^'\\\n])'", text[index:])
        if match:
            return Token("lit", "'…'", line), index + match.end()
        return None
    if text[index] != "'":
        return None
    match = re.match(r"'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\n])'", text[index:])
    if match:
        return Token("lit", "'…'", line), index + match.end()
    end = index + 1
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    return Token("ident", text[index:end], line), end


def _match_number(text: str, index: int, line: int) -> tuple[Token, int]:
    end = index
    while end < len(text) and (text[end].isalnum() or text[end] in "_."):
        if text[end] == "." and (end + 1 >= len(text) or not text[end + 1].isdigit()):
            break
        end += 1
    return Token("lit", text[index:end], line), end


def _match_identifier(text: str, index: int, line: int) -> tuple[Token, int]:
    end = index + 1
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    return Token("ident", text[index:end], line), end


def _match_punctuation(text: str, index: int, line: int) -> tuple[Token, int]:
    for size in (3, 2):
        candidate = text[index : index + size]
        if candidate in _PUNCT:
            return Token("punct", candidate, line), index + size
    return Token("punct", text[index], line), index + 1


def _lex(text: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    while index < len(text):
        char = text[index]
        if char == "\n":
            line += 1
            index += 1
            continue
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            index = _skip_line_comment(text, index)
            continue
        if text.startswith("/*", index):
            index, line = _skip_block_comment(text, index, line)
            continue
        raw = _match_raw_string(text, index, line)
        if raw is not None:
            token, index, line = raw
            tokens.append(token)
            continue
        string = _match_string(text, index, line)
        if string is not None:
            token, index, line = string
            tokens.append(token)
            continue
        char_token = _match_char(text, index, line)
        if char_token is not None:
            token, index = char_token
            tokens.append(token)
            continue
        if char.isdigit():
            token, index = _match_number(text, index, line)
            tokens.append(token)
            continue
        if char.isalpha() or char == "_":
            token, index = _match_identifier(text, index, line)
            tokens.append(token)
            continue
        token, index = _match_punctuation(text, index, line)
        tokens.append(token)
    return tokens


def _pairs(tokens: tuple[Token, ...], open_value: str, close_value: str) -> dict[int, int]:
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for index, token in enumerate(tokens):
        if token.value == open_value:
            stack.append(index)
        elif token.value == close_value and stack:
            pairs[stack.pop()] = index
    return pairs


def _starts_item(token: Token | None) -> bool:
    if token is None:
        return True
    if token.value in {"{", "}", ";", "]"}:
        return True
    return token.kind == "ident" and token.value in {
        "pub",
        "unsafe",
        "async",
        "const",
        "extern",
        "default",
    }


def _ends_expression(token: Token) -> bool:
    return token.kind in {"ident", "lit"} or token.value in {")", "]", "}"}


def _attr_path(text: str) -> list[str]:
    body = text.strip()[1:].strip()
    if body.startswith("!"):
        body = body[1:].strip()
    if body.startswith("["):
        body = body[1:]
    if body.endswith("]"):
        body = body[:-1]
    path = body.split("(", 1)[0]
    return [part for part in re.split(r"[^A-Za-z0-9_]+", path) if part]


def _is_test_attr(text: str) -> bool:
    parts = _attr_path(text)
    return bool(parts) and parts[-1] in _TEST_ATTRS


def _is_ignore_attr(text: str) -> bool:
    return "ignore" in re.split(r"[^a-z0-9_]+", text.lower())


def _type_name(tokens: list[Token]) -> str:
    depth = 0
    for_index: int | None = None
    end_index = len(tokens)
    for index, token in enumerate(tokens):
        value = token.value
        if value == "<":
            depth += 1
            continue
        if value.startswith(">"):
            depth = max(0, depth - value.count(">"))
            continue
        if depth:
            continue
        if value == "where":
            end_index = index
            break
        if value == "for":
            for_index = index
    header = tokens[for_index + 1 : end_index] if for_index is not None else tokens[:end_index]
    depth = 0
    names: list[str] = []
    for token in header:
        value = token.value
        if value == "<":
            depth += 1
            continue
        if value.startswith(">"):
            depth = max(0, depth - value.count(">"))
            continue
        if depth:
            continue
        if token.kind == "ident" and value != "impl":
            names.append(value)
    return names[-1] if names else ""


class _Analyzer:
    def __init__(self, tokens: tuple[Token, ...]) -> None:
        self.t = tokens
        self.brace = _pairs(tokens, "{", "}")
        self.paren = _pairs(tokens, "(", ")")
        self.bracket = _pairs(tokens, "[", "]")
        self.api: dict[str, str] = {}
        self.complexity: dict[str, int] = {}
        self.tests: set[str] = set()
        self.skips: set[str] = set()
        self.assertions = 0
        self._scan(0, len(tokens), (), None, False)

    def _scan(
        self,
        start: int,
        end: int,
        module: tuple[str, ...],
        owner: str | None,
        default_public: bool,
    ) -> None:
        index = start
        while index < end:
            token = self.t[index]
            if token.kind == "ident":
                if token.value == "macro_rules":
                    index = self._skip_macro(index, end)
                    continue
                if token.value == "macro":
                    opened = self._block_open(index + 1, end)
                    index = self.brace[opened] + 1 if opened is not None else index + 1
                    continue
                if (
                    index + 2 < end
                    and self.t[index + 1].value == "!"
                    and self.t[index + 2].value in {"{", "(", "["}
                ):
                    close = self._closing(index + 2)
                    if close is not None:
                        index = close + 1
                        continue
            head = self._head(index, end)
            if head is None:
                index += 1
                continue
            index = self._item(head, end, module, owner, default_public)

    def _closing(self, index: int) -> int | None:
        value = self.t[index].value
        if value == "{":
            return self.brace.get(index)
        if value == "(":
            return self.paren.get(index)
        if value == "[":
            return self.bracket.get(index)
        return None

    def _skip_macro(self, start: int, end: int) -> int:
        opened = self._block_open(start + 1, end)
        if opened is None:
            return start + 1
        return self.brace[opened] + 1

    def _block_open(self, start: int, end: int) -> int | None:
        index = start
        while index < end:
            value = self.t[index].value
            if value == "{":
                return index
            if value in {"(", "["}:
                close = self._closing(index)
                if close is None:
                    return None
                index = close + 1
                continue
            if value in {";", "}"}:
                return None
            index += 1
        return None

    def _attribute_end(self, index: int) -> int | None:
        cursor = index + 1
        if cursor < len(self.t) and self.t[cursor].value == "!":
            cursor += 1
        if cursor >= len(self.t) or self.t[cursor].value != "[":
            return None
        return self.bracket.get(cursor)

    def _attribute_text(self, start: int, close: int) -> str:
        return " ".join(token.value for token in self.t[start : close + 1])

    def _const_is_modifier(self, index: int, end: int) -> bool:
        cursor = index + 1
        while cursor < end:
            value = self.t[cursor].value
            if value in {"unsafe", "async"}:
                cursor += 1
                continue
            if value == "extern":
                cursor += 1
                if cursor < end and self.t[cursor].kind == "lit":
                    cursor += 1
                continue
            break
        return cursor < end and self.t[cursor].value == "fn"

    def _attributes(self, index: int, end: int) -> tuple[list[str], int] | None:
        attrs: list[str] = []
        cursor = index
        while cursor < end and self.t[cursor].value == "#":
            close = self._attribute_end(cursor)
            if close is None:
                return None
            attrs.append(self._attribute_text(cursor, close))
            cursor = close + 1
        return attrs, cursor

    def _modifiers(self, index: int, end: int) -> tuple[bool, int] | None:
        public = False
        cursor = index
        while cursor < end:
            value = self.t[cursor].value
            if value == "pub":
                public = True
                if cursor + 1 < end and self.t[cursor + 1].value == "(":
                    close = self.paren.get(cursor + 1)
                    if close is None:
                        return None
                    cursor = close + 1
                    public = False
                    continue
                cursor += 1
                continue
            if value in _MODIFIERS:
                cursor += 1
                continue
            if value == "extern":
                cursor += 1
                if cursor < end and self.t[cursor].kind == "lit":
                    cursor += 1
                continue
            if value == "const" and self._const_is_modifier(cursor, end):
                cursor += 1
                continue
            break
        return public, cursor

    def _head(self, index: int, end: int) -> _Head | None:
        attributes = self._attributes(index, end)
        if attributes is None:
            return None
        attrs, cursor = attributes
        modifiers = self._modifiers(cursor, end)
        if modifiers is None:
            return None
        public, cursor = modifiers
        if cursor >= end or self.t[cursor].kind != "ident" or self.t[cursor].value not in _ITEMS:
            return None
        return _Head(cursor, tuple(attrs), public)

    def _item(
        self,
        head: _Head,
        end: int,
        module: tuple[str, ...],
        owner: str | None,
        default_public: bool,
    ) -> int:
        keyword = self.t[head.keyword].value
        if keyword == "use":
            return self._skip_statement(head.keyword, end)
        if keyword == "mod":
            return self._module(head, end, module)
        if keyword == "impl":
            return self._impl(head, end, module)
        if keyword == "trait":
            return self._trait(head, end, module)
        if keyword == "fn":
            return self._function(head, end, module, owner, default_public)
        return self._simple(head, end, module, owner, default_public)

    def _skip_statement(self, start: int, end: int) -> int:
        index = start + 1
        while index < end:
            value = self.t[index].value
            if value == ";":
                return index + 1
            if value in {"(", "[", "{"}:
                close = self._closing(index)
                if close is None:
                    return index + 1
                index = close + 1
                continue
            if value == "}":
                return index
            index += 1
        return end

    def _module(self, head: _Head, end: int, module: tuple[str, ...]) -> int:
        name_index = head.keyword + 1
        if name_index >= end or self.t[name_index].kind != "ident":
            return head.keyword + 1
        name = self.t[name_index].value
        cursor = name_index + 1
        if cursor < end and self.t[cursor].value == "{":
            close = self.brace.get(cursor)
            if close is None:
                return end
            self._scan(cursor + 1, close, module + (name,), None, False)
            return close + 1
        return cursor + 1

    def _impl(self, head: _Head, end: int, module: tuple[str, ...]) -> int:
        opened = self._block_open(head.keyword + 1, end)
        if opened is None:
            return head.keyword + 1
        owner = _type_name(list(self.t[head.keyword + 1 : opened])) or "impl"
        close = self.brace[opened]
        self._scan(opened + 1, close, module, owner, False)
        return close + 1

    def _trait(self, head: _Head, end: int, module: tuple[str, ...]) -> int:
        name_index = head.keyword + 1
        if name_index >= end or self.t[name_index].kind != "ident":
            return head.keyword + 1
        name = self.t[name_index].value
        if head.public:
            self.api["::".join(module + (name,))] = "trait"
        opened = self._block_open(name_index + 1, end)
        if opened is None:
            return name_index + 1
        close = self.brace[opened]
        self._scan(opened + 1, close, module, name, True)
        return close + 1

    def _item_terminator(self, start: int, end: int) -> tuple[str, int]:
        index = start
        while index < end:
            value = self.t[index].value
            if value in {"(", "["}:
                close = self._closing(index)
                if close is None:
                    return "none", end
                index = close + 1
                continue
            if value == "{":
                return "body", index
            if value == ";":
                return "sig", index
            if value == "}":
                return "none", index
            index += 1
        return "none", end

    def _function(
        self,
        head: _Head,
        end: int,
        module: tuple[str, ...],
        owner: str | None,
        default_public: bool,
    ) -> int:
        name_index = head.keyword + 1
        if name_index >= end or self.t[name_index].kind != "ident":
            return head.keyword + 1
        name = self.t[name_index].value
        kind, position = self._item_terminator(name_index, end)
        if kind == "none":
            return position + 1
        signature = "".join(token.value for token in self.t[name_index:position])
        parts = module + ((owner,) if owner else ()) + (name,)
        key = "::".join(parts)
        if head.public or default_public:
            self.api[key] = signature
        is_test = any(_is_test_attr(attribute) for attribute in head.attrs)
        ignored = any(_is_ignore_attr(attribute) for attribute in head.attrs)
        if kind == "body":
            close = self.brace[position]
            self.complexity[key] = 1 + self._branch_score(position + 1, close)
            if is_test:
                self.tests.add(key)
                if ignored:
                    self.skips.add(key)
                self.assertions += self._assertion_count(position + 1, close)
            self._scan(position + 1, close, module, None, False)
            return close + 1
        if is_test:
            self.tests.add(key)
            if ignored:
                self.skips.add(key)
        return position + 1

    def _simple(
        self,
        head: _Head,
        end: int,
        module: tuple[str, ...],
        owner: str | None,
        default_public: bool,
    ) -> int:
        keyword = self.t[head.keyword].value
        name_index = head.keyword + 1
        if keyword == "static" and name_index < end and self.t[name_index].value == "mut":
            name_index += 1
        if name_index >= end or self.t[name_index].kind != "ident":
            return head.keyword + 1
        name = self.t[name_index].value
        kind, position = self._item_terminator(name_index, end)
        if kind == "none":
            return position + 1
        signature = "".join(token.value for token in self.t[name_index:position])
        parts = module + ((owner,) if owner else ()) + (name,)
        if head.public or default_public:
            self.api["::".join(parts)] = signature
        if kind == "body":
            return self.brace[position] + 1
        return position + 1

    def _branch_score(self, start: int, end: int) -> int:
        score = 0
        previous = self.t[start - 1] if start > 0 else None
        index = start
        while index < end:
            token = self.t[index]
            value = token.value
            if value == "fn" and token.kind == "ident" and _starts_item(previous):
                kind, position = self._item_terminator(index + 1, end)
                if kind == "body":
                    close = self.brace[position]
                    previous = self.t[close]
                    index = close + 1
                else:
                    previous = self.t[position]
                    index = position + 1
                continue
            if value == "if":
                score += 1
            elif value in {"for", "while", "loop"}:
                score += 1
            elif value == "match":
                opened = self._block_open(index + 1, end)
                if opened is not None:
                    score += self._match_arms(opened)
            elif value in {"&&", "||"} and previous is not None and _ends_expression(previous):
                score += 1
            previous = token
            index += 1
        return score

    def _match_arms(self, opened: int) -> int:
        close = self.brace.get(opened)
        if close is None:
            return 0
        depth = 0
        arms = 0
        for index in range(opened + 1, close):
            value = self.t[index].value
            if value in {"(", "[", "{"}:
                depth += 1
            elif value in {")", "]", "}"}:
                depth = max(0, depth - 1)
            elif value == "=>" and depth == 0:
                arms += 1
        return arms

    def _assertion_count(self, start: int, end: int) -> int:
        count = 0
        for index in range(start, end - 1):
            token = self.t[index]
            if token.kind != "ident":
                continue
            if not (token.value.startswith("assert") or token.value.startswith("prop_assert")):
                continue
            if self.t[index + 1].value == "!":
                count += 1
        return count


def parse_text(text: str | None) -> RustFile | None:
    if text is None:
        return None
    tokens = tuple(_lex(text))
    analyzer = _Analyzer(tokens)
    return RustFile(
        tokens=tokens,
        api=analyzer.api,
        complexity=analyzer.complexity,
        tests=frozenset(analyzer.tests),
        assertions=analyzer.assertions,
        skips=frozenset(analyzer.skips),
    )


def public_api(tree: RustFile | None) -> dict[str, str]:
    return dict(tree.api) if tree is not None else {}


def complexity(tree: RustFile | None) -> dict[str, int]:
    return dict(tree.complexity) if tree is not None else {}


def test_info(tree: RustFile | None) -> TestInfo:
    if tree is None:
        return TestInfo(frozenset(), 0, frozenset())
    return TestInfo(tree.tests, tree.assertions, tree.skips)


def is_stdlib(module: str) -> bool:
    return module in STDLIB


def _use_entries(tokens: tuple[Token, ...], start: int, close: int) -> list[tuple[int, int]]:
    entries: list[tuple[int, int]] = []
    depth = 0
    entry_start = start
    index = start
    while index < close:
        value = tokens[index].value
        if value in {"{", "(", "["}:
            depth += 1
        elif value in {"}", ")", "]"}:
            depth = max(0, depth - 1)
        elif value == "," and depth == 0:
            entries.append((entry_start, index))
            entry_start = index + 1
        index += 1
    if entry_start < close:
        entries.append((entry_start, close))
    return entries


def _parse_use(
    tokens: tuple[Token, ...],
    start: int,
    closing: dict[int, int],
    prefix: list[str],
) -> tuple[list[list[str]], int]:
    paths: list[list[str]] = []
    segments = list(prefix)
    expanded = False
    index = start
    while index < len(tokens):
        token = tokens[index]
        value = token.value
        if value == "::":
            index += 1
            continue
        if value == ";":
            index += 1
            break
        if value == "{":
            close = closing.get(index)
            if close is None:
                break
            for entry_start, _ in _use_entries(tokens, index + 1, close):
                inner, _ = _parse_use(tokens, entry_start, closing, segments)
                paths.extend(inner)
            expanded = True
            index = close + 1
            continue
        if value == "as":
            index += 2
            continue
        if value == "*":
            paths.append(segments + ["*"])
            expanded = True
            index += 1
            continue
        if token.kind == "ident":
            segments.append(value)
            index += 1
            continue
        index += 1
    if segments and not expanded:
        paths.append(segments)
    return paths, index


def imports(tree: RustFile) -> list[ImportRef]:
    tokens = tree.tokens
    closing = _pairs(tokens, "{", "}")
    roots: list[tuple[str, int]] = []
    for index, token in enumerate(tokens):
        if token.kind != "ident":
            continue
        if token.value == "use":
            for path in _parse_use(tokens, index + 1, closing, [])[0]:
                if path:
                    roots.append((path[0], token.line))
            continue
        if (
            token.value == "extern"
            and index + 2 < len(tokens)
            and tokens[index + 1].value == "crate"
            and tokens[index + 2].kind == "ident"
        ):
            roots.append((tokens[index + 2].value, token.line))
    refs: list[ImportRef] = []
    seen: set[str] = set()
    for module, line in roots:
        if module in {"crate", "self", "super", "*"} or module in seen:
            continue
        seen.add(module)
        refs.append(ImportRef(module=module, level=0, line=line))
    return refs


def _read_manifest(root: Path, rel: str) -> tuple[str, dict[str, tuple[str, int]]]:
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
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
        _, deps = _read_manifest(root, rel)
        for dependency, site in deps.items():
            found.setdefault(dependency, site)
    return found


def _repo_files(root: Path) -> tuple[list[str], list[str]]:
    manifests: list[str] = []
    sources: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        relative = os.path.relpath(dirpath, root)
        relative = "" if relative == "." else Path(relative).as_posix()
        for name in sorted(filenames):
            rel = f"{relative}/{name}" if relative else name
            if name == "Cargo.toml":
                manifests.append(rel)
            elif name.endswith(".rs"):
                sources.append(rel)
    return manifests, sources


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
            self._manifest(rel)
        self._load_sources(sources)

    def _load_sources(self, sources: list[str]) -> None:
        for rel in sources:
            try:
                text = (self.root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            tree = parse_text(text)
            if tree is None:
                continue
            self.files[rel] = tree
            self.modules.add(Path(rel).stem)
            tokens = tree.tokens
            for index, token in enumerate(tokens[:-1]):
                if token.kind == "ident" and token.value == "mod":
                    following = tokens[index + 1]
                    if following.kind == "ident" and following.value not in {"crate", "self", "super"}:
                        self.modules.add(following.value)

    def _manifest(self, rel: str) -> None:
        name, deps = _read_manifest(self.root, rel)
        if name:
            self.crates[posixpath.dirname(rel)] = name
        for dependency, site in deps.items():
            self.deps.setdefault(dependency, site)

    def crate_of(self, rel: str) -> str:
        directory = posixpath.dirname(rel)
        while True:
            name = self.crates.get(directory)
            if name:
                return name
            if not directory:
                return ""
            directory = posixpath.dirname(directory)


def _is_dependency_table(table: str) -> bool:
    return (
        re.fullmatch(
            r"(?:workspace\.|target\..+\.)?(?:dev-|build-)?dependencies",
            table,
        )
        is not None
    )


def package_edges(root: Path, config: Config) -> tuple[set[tuple[str, str]], set[str]]:
    index = RepoIndex(root)
    packages = set(index.crates.values())
    edges: set[tuple[str, str]] = set()
    for rel, tree in index.files.items():
        if config.is_excluded(rel) or config.is_test(rel):
            continue
        package = index.crate_of(rel)
        if not package:
            continue
        for ref in imports(tree):
            if ref.module in packages and ref.module != package:
                edges.add((package, ref.module))
    return edges, packages


def project_imports(root: Path, config: Config) -> dict[str, tuple[str, int]]:
    index = RepoIndex(root)
    found: dict[str, tuple[str, int]] = dict(index.deps)
    for rel, tree in index.files.items():
        if config.is_excluded(rel) or config.is_test(rel):
            continue
        for ref in imports(tree):
            if ref.level != 0 or not ref.module:
                continue
            found.setdefault(ref.module, (rel, ref.line))
    return found


def local_packages(root: Path, _config: Config) -> set[str]:
    index = RepoIndex(root)
    return set(index.crates.values()) | set(index.modules)
