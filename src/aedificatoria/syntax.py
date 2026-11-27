"""Extract the first syntax error from a tree-sitter tree."""

from __future__ import annotations

from tree_sitter import Node, Tree


def _first_error(node: Node) -> Node:
    if node.is_error or node.is_missing:
        return node
    for child in node.children:
        if child.has_error:
            return _first_error(child)
    return node


def tree_sitter_error(tree: Tree, text: str) -> tuple[int, str, str] | None:
    root = tree.root_node
    if not root.has_error:
        return None
    node = _first_error(root)
    line = node.start_point.row + 1
    lines = text.splitlines()
    evidence = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    kind = "missing syntax" if node.is_missing else "syntax error"
    return line, kind, evidence
