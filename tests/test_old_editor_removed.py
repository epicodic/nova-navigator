"""The editor of the navigator is `nova_editor`; the old module file of the navigator's own editor is gone (REQ-20)."""

from __future__ import annotations

from pathlib import Path

import nova_navigator


def test_the_old_editor_module_file_is_gone() -> None:
    package_dir = Path(nova_navigator.__file__).parent
    assert not (package_dir / "editor.py").exists()
