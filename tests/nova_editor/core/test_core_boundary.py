"""REQ-17: nova_editor.core imports no Textual module."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).parents[3] / "src" / "nova_editor" / "core"
ALLOWED_THIRD_PARTY = {"rich"}
STDLIB = set(sys.stdlib_module_names)


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_core_sources_import_only_stdlib_rich_and_core() -> None:
    files = sorted(CORE.glob("*.py"))
    assert files
    for path in files:
        for module in imported_modules(path):
            top = module.split(".")[0]
            allowed = top in STDLIB or top in ALLOWED_THIRD_PARTY or module.startswith("nova_editor.core") or module == "__future__"
            assert allowed, f"{path.name} imports {module}"
            assert top != "textual", f"{path.name} imports Textual"


STUB_IMPORT = """
import importlib
import pathlib
import sys
import types

root = pathlib.Path(sys.argv[1])
stub = types.ModuleType("nova_editor")
stub.__path__ = [str(root / "src" / "nova_editor")]  # a parent package whose __init__ does not run
sys.modules["nova_editor"] = stub
for name in ("byte_source", "text_width", "line_index", "long_line_index"):
    importlib.import_module("nova_editor.core." + name)
importlib.import_module("nova_editor.core")
bad = sorted(m for m in sys.modules if m == "textual" or m.startswith("textual.") or m.startswith("nova_navigator"))
print("BAD:" + ",".join(bad) if bad else "OK")
"""


def test_core_modules_load_without_textual() -> None:
    root = Path(__file__).parents[3]
    result = subprocess.run([sys.executable, "-c", STUB_IMPORT, str(root)], capture_output=True, text=True, check=False, cwd=root)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK", result.stdout


PACKAGE = Path(__file__).parents[3] / "src" / "nova_editor"
FORBIDDEN = ("textual.widgets._text_area", "textual.document")


def test_package_never_imports_private_textual_text_area_modules() -> None:
    """Static scan: the vendored layers replace `textual.widgets._text_area` and `textual.document`, so nothing imports them."""
    files = sorted(PACKAGE.rglob("*.py"))
    assert files
    for path in files:
        for module in imported_modules(path):
            for forbidden in FORBIDDEN:
                hit = module == forbidden or module.startswith(forbidden + ".")
                assert not hit, f"{path.relative_to(PACKAGE)} imports {module}"
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "textual.widgets":
                assert all(alias.name != "_text_area" for alias in node.names), f"{path.relative_to(PACKAGE)} imports textual.widgets._text_area"
            if isinstance(node, ast.ImportFrom) and node.module == "textual":
                assert all(alias.name != "document" for alias in node.names), f"{path.relative_to(PACKAGE)} imports textual.document"


def test_core_text_never_mentions_a_textual_import() -> None:
    """Static text scan of `core`: no `import textual` or `from textual` line, however it is nested."""
    for path in sorted(CORE.glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            assert not stripped.startswith(("import textual", "from textual")), f"{path.name}:{number}"


def test_save_modules_never_import_document_or_widget() -> None:
    checked = 0
    for name in ("save.py", "save_layout.py", "rebase.py"):
        path = CORE / name
        if not path.exists():
            continue
        checked += 1
        for module in imported_modules(path):
            assert not module.startswith(("nova_editor.document", "nova_editor.widget")), f"{name} imports {module}"
    assert checked >= 2
