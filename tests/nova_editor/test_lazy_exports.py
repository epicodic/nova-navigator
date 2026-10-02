"""`nova_editor` exports its widget names lazily so that `import nova_editor.core` does not load Textual (DEC-15)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def run(code: str) -> str:
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_package_import_loads_no_textual() -> None:
    assert run("import sys, nova_editor; print(any(m == 'textual' or m.startswith('textual.') for m in sys.modules))") == "False"


def test_the_names_resolve_on_first_access() -> None:
    code = (
        "import sys, nova_editor\n"
        "from nova_editor import DEFAULT_HIGHLIGHT_LIMIT, LazyConfig, NovaTextArea\n"
        "print(NovaTextArea.__name__, LazyConfig.__name__, DEFAULT_HIGHLIGHT_LIMIT > 0, 'textual' in sys.modules)"
    )
    assert run(code) == "NovaTextArea LazyConfig True True"


def test_dir_lists_the_public_names() -> None:
    assert run("import nova_editor; print(sorted(set(nova_editor.__all__) & set(dir(nova_editor))))") == "['DEFAULT_HIGHLIGHT_LIMIT', 'LazyConfig', 'NovaTextArea']"


def test_an_unknown_name_raises_attribute_error() -> None:
    assert run("import nova_editor\ntry:\n    nova_editor.nope\nexcept AttributeError as e:\n    print('AttributeError', e)").startswith("AttributeError")
