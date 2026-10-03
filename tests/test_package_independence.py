"""Guard the package direction: ``nova_widgets`` and ``nova_editor`` must not depend on ``nova_navigator`` (REQ-8)."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SRC = _ROOT / "src"
_GUARDED_PACKAGES = ("nova_widgets", "nova_editor")
_VFS_MODULES = {"vfs", "vfs2"}


def _imported_modules(source: str) -> list[str]:
    """Return the dotted module names imported by ``source`` (relative imports are returned with their dots)."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append("." * node.level + (node.module or ""))
    return modules


def _forbidden_navigator_imports(source: str) -> list[str]:
    return [m for m in _imported_modules(source) if m.split(".")[0] == "nova_navigator"]


def _vfs_imports(source: str) -> list[str]:
    return [m for m in _imported_modules(source) if _VFS_MODULES & set(m.split("."))]


def _python_files(package: str) -> list[Path]:
    return sorted((_SRC / package).rglob("*.py"))


# --- scanner self-tests: the scanner must fail on forbidden imports and ignore comments and strings ---


@pytest.mark.parametrize(
    "source",
    [
        "import nova_navigator",
        "import nova_navigator.response",
        "from nova_navigator import Response",
        "from nova_navigator.dialogs.dialog import Dialog",
        "def f() -> None:\n    from nova_navigator.response import Response\n",
    ],
)
def test_scanner_detects_navigator_imports(source: str) -> None:
    assert _forbidden_navigator_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "# import nova_navigator",
        '"""from nova_navigator import Response"""',
        "x = 'import nova_navigator'",
        "import nova_widgets",
        "from .response import Response",
    ],
)
def test_scanner_ignores_comments_strings_and_other_imports(source: str) -> None:
    assert not _forbidden_navigator_imports(source)


@pytest.mark.parametrize(
    ("source", "expected_count"),
    [
        ("import nova_navigator.vfs", 1),
        ("from nova_navigator.vfs import VPath", 1),
        ("from .vfs2 import VPath", 1),
        ("from . import vfs", 0),
    ],
)
def test_scanner_detects_vfs_imports(source: str, expected_count: int) -> None:
    assert len(_vfs_imports(source)) == expected_count


def test_scanner_vfs_check_ignores_unrelated_names() -> None:
    assert not _vfs_imports("from .vfs_like_name import x\nimport os.path")


# --- real checks ---


@pytest.mark.parametrize("package", _GUARDED_PACKAGES)
def test_package_sources_do_not_import_nova_navigator(package: str) -> None:
    files = _python_files(package)
    assert files, f"no sources found for {package}"
    offenders = {str(path.relative_to(_ROOT)): bad for path in files if (bad := _forbidden_navigator_imports(path.read_text(encoding="utf-8")))}
    assert not offenders, f"{package} imports nova_navigator: {offenders}"


def test_nova_editor_sources_do_not_use_the_vfs() -> None:
    offenders = {str(path.relative_to(_ROOT)): bad for path in _python_files("nova_editor") if (bad := _vfs_imports(path.read_text(encoding="utf-8")))}
    assert not offenders, f"nova_editor uses the VFS: {offenders}"


@pytest.mark.parametrize("package", _GUARDED_PACKAGES)
def test_importing_package_does_not_load_nova_navigator(package: str) -> None:
    code = f"import sys\nimport {package}\nsys.exit(1 if 'nova_navigator' in sys.modules else 0)\n"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=str(_ROOT), timeout=60)
    assert result.returncode == 0, f"importing {package} loaded nova_navigator: {result.stderr}"
