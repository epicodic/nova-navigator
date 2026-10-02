"""Golden trace of the lazy (`open`) path: cursor, selections, line access for an unedited lazily-opened document."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.widget import NovaTextArea

GOLDEN_LAZY = Path(__file__).parent / "golden" / "lazy_trace.json"

LAZY_TEXT = "hello\nworld\nthis is a test\nwith multiple lines\n"

LAZY_SEQUENCE = [
    ("start state", None),
    ("cursor down", "down"),
    ("cursor down", "down"),
    ("cursor right", "right"),
    ("cursor end", "end"),
    ("cursor home", "home"),
    ("shift right", "shift+right"),
    ("shift down", "shift+down"),
    ("shift end", "shift+end"),
]


class LazyTraceApp(App[None]):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        ta = NovaTextArea.open(self._path, id="ta")
        yield ta


async def record_lazy() -> list[dict[str, object]]:
    """Record lazy document operations: cursor moves, selections, line access."""
    trace: list[dict[str, object]] = []

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(LAZY_TEXT)
        temp_path = Path(f.name)

    try:
        app = LazyTraceApp(temp_path)
        async with app.run_test(size=(40, 8)) as pilot:
            await pilot.pause(delay=0.5)
            area = app.query_one(NovaTextArea)
            await pilot.pause()

            for step_name, key in LAZY_SEQUENCE:
                if key is not None:
                    await pilot.press(key)
                    await pilot.pause()

                cursor_loc = list(area.cursor_location)
                selection = [list(area.selection.start), list(area.selection.end)]
                line_count = area.document.line_count

                try:
                    current_line = area.document.get_line(area.cursor_location[0])
                except (IndexError, AttributeError):
                    current_line = None

                try:
                    end_location = list(area.document.end)
                except (IndexError, AttributeError):
                    end_location = None

                trace.append(
                    {
                        "step": step_name,
                        "cursor": cursor_loc,
                        "selection": selection,
                        "line_count": line_count,
                        "current_line": current_line,
                        "end_location": end_location,
                    }
                )

        return trace
    finally:
        temp_path.unlink()


@pytest.mark.asyncio
async def test_lazy_trace_matches_golden() -> None:
    golden = json.loads(GOLDEN_LAZY.read_text())
    assert await record_lazy() == golden["trace"]


if __name__ == "__main__":
    lazy_trace = asyncio.run(record_lazy())
    lazy_data = {"trace": lazy_trace}
    GOLDEN_LAZY.write_text(json.dumps(lazy_data, indent=2))
    print(f"Golden lazy trace recorded to {GOLDEN_LAZY}")
