"""The attribute docstrings of the widget module sit directly under the attribute they describe."""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

WIDGET = Path(__file__).parents[2] / "src" / "nova_editor" / "widget" / "_text_area.py"


def _is_string(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _orphans(body: list[ast.stmt]) -> list[int]:
    """Lines of string statements that do not directly follow an assignment (a docstring of nothing)."""
    return [node.lineno for before, node in pairwise(body) if _is_string(node) and not isinstance(before, ast.Assign | ast.AnnAssign)]


def test_no_module_level_string_is_an_orphan() -> None:
    assert _orphans(ast.parse(WIDGET.read_text()).body) == []


def test_each_save_attribute_has_its_own_docstring() -> None:
    tree = ast.parse(WIDGET.read_text())
    init = next(node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "NovaTextArea" for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    docs: dict[str, str] = {}
    for before, node in pairwise(init.body):
        if _is_string(node) and isinstance(before, ast.Assign | ast.AnnAssign):
            target = before.targets[0] if isinstance(before, ast.Assign) else before.target
            if isinstance(target, ast.Attribute) and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                docs[target.attr] = str(node.value.value)
    assert "running save" in docs["_save_run"]
    assert "Counts the starts and ends" in docs["_save_epoch"]
