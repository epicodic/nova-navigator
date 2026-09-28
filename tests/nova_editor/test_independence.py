"""Tests to verify nova_editor is independent of nova_navigator."""

import subprocess
import sys
from pathlib import Path


def test_no_nova_navigator_imports_in_source() -> None:
    """Verify no source files in nova_editor import nova_navigator."""
    from pathlib import Path

    nova_editor_dir = Path(__file__).parent.parent.parent / "src" / "nova_editor"

    for py_file in nova_editor_dir.rglob("*.py"):
        with open(py_file) as f:
            content = f.read()
            assert "import nova_navigator" not in content, f"{py_file} imports nova_navigator"
            assert "from nova_navigator" not in content, f"{py_file} imports from nova_navigator"


def test_importing_nova_editor_does_not_import_nova_navigator() -> None:
    """Verify importing nova_editor doesn't bring in nova_navigator."""
    # Run in a subprocess to get a clean Python environment
    code = """
import sys

# Import nova_editor
import nova_editor

# Check that nova_navigator is not in sys.modules
if 'nova_navigator' in sys.modules:
    print('ERROR: nova_navigator was imported')
    sys.exit(1)

print('OK: nova_navigator not imported')
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).parent.parent.parent),
    )

    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    assert "OK" in result.stdout, f"unexpected output: {result.stdout}"
