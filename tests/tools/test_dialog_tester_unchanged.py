"""The dialog_tester listing stays as recorded before the editor app changes (DEC-26 note 6)."""

import subprocess
import sys
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden" / "dialog_tester_list.txt"


def test_dialog_tester_listing_is_unchanged() -> None:
    result = subprocess.run([sys.executable, "-m", "tools.dialog_tester", "--list"], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0
    assert result.stdout == GOLDEN.read_text()
