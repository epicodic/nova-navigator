"""Golden trace of the stock (`text=`) path: cursor, scroll and rendered lines after scripted keys (recorded on BASE 2a7bb25)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.widget import NovaTextArea

GOLDEN = Path(__file__).parent / "golden" / "stock_trace.json"
GOLDEN_EDIT = Path(__file__).parent / "golden" / "stock_edit_trace.json"
TEXT = "\n".join(
    [
        "short line",
        "\ttabbed\tline with 日本語 and é combining",
        "word " * 40,
        "",
        "x" * 150,
        "last line",
    ],
)
KEYS = ["down", "down", "end", "home", "right", "right", "pagedown", "up", "up", "ctrl+right", "ctrl+left", "pageup", "shift+right", "shift+down"]

EDIT_TEXT = "hello world"
EDIT_SEQUENCE = [
    ("insert at start", (0, 0), lambda ta: ta.insert(">>> ", (0, 0))),
    ("cursor move to end", None, lambda ta: ta.action_cursor_line_end()),
    ("insert at cursor", None, lambda ta: ta.insert(" <<<", None)),
    ("select word via shift+ctrl+right", None, lambda ta: ta.action_cursor_word_right(True)),
    ("delete selection", None, lambda ta: ta.delete(ta.selection.start, ta.selection.end)),
    ("type character", None, lambda ta: ta.insert("X", None)),
    ("backspace", None, lambda ta: ta.action_delete_left()),
    ("delete right", None, lambda ta: ta.action_delete_right()),
    ("enter newline", None, lambda ta: ta.insert("\n", None)),
    ("type on new line", None, lambda ta: ta.insert("new", None)),
    ("undo 1", None, lambda ta: ta.undo()),
    ("undo 2", None, lambda ta: ta.undo()),
    ("undo 3", None, lambda ta: ta.undo()),
    ("redo 1", None, lambda ta: ta.redo()),
    ("redo 2", None, lambda ta: ta.redo()),
    ("select all", None, lambda ta: ta.action_select_all()),
    ("copy", None, lambda ta: ta.action_copy()),
    ("cursor to start", None, lambda ta: ta.action_cursor_line_start()),
    ("delete line start", None, lambda ta: ta.action_delete_to_start_of_line()),
    ("paste", None, lambda ta: ta.action_paste()),
    ("select all then cut", None, lambda ta: (ta.action_select_all(), ta.action_cut())[1]),
]


class TraceApp(App[None]):
    def __init__(self, *, soft_wrap: bool) -> None:
        super().__init__()
        self._soft_wrap = soft_wrap

    def compose(self) -> ComposeResult:
        yield NovaTextArea(TEXT, soft_wrap=self._soft_wrap, id="ta")


class EditTraceApp(App[None]):
    def compose(self) -> ComposeResult:
        yield NovaTextArea(EDIT_TEXT, soft_wrap=False, id="ta")


async def record(soft_wrap: bool) -> list[dict[str, object]]:
    trace: list[dict[str, object]] = []
    app = TraceApp(soft_wrap=soft_wrap)
    async with app.run_test(size=(40, 8)) as pilot:
        await pilot.pause()
        area = app.query_one(NovaTextArea)
        for key in KEYS:
            await pilot.press(key)
            await pilot.pause()
            rows = ["".join(seg.text for seg in area.render_line(y)) for y in range(area.size.height)]
            trace.append({"key": key, "sel": [list(area.selection.start), list(area.selection.end)], "scroll": [area.scroll_x, area.scroll_y], "rows": rows})
    return trace


async def record_edit() -> list[dict[str, object]]:
    """Record edit operations with text, selection, and replaced_text."""
    trace: list[dict[str, object]] = []
    app = EditTraceApp()
    async with app.run_test(size=(40, 8)) as pilot:
        await pilot.pause()
        area = app.query_one(NovaTextArea)

        for step_name, _location, operation in EDIT_SEQUENCE:
            result = operation(area)
            await pilot.pause()
            trace.append(
                {
                    "step": step_name,
                    "text": area.text,
                    "selection": [list(area.selection.start), list(area.selection.end)],
                    "replaced_text": result.replaced_text if hasattr(result, "replaced_text") else None,
                }
            )

    return trace


@pytest.mark.asyncio
@pytest.mark.parametrize("soft_wrap", [False, True])
async def test_stock_trace_matches_golden(soft_wrap: bool) -> None:
    golden = json.loads(GOLDEN.read_text())
    assert await record(soft_wrap) == golden[str(soft_wrap)]


@pytest.mark.asyncio
async def test_stock_edit_trace_matches_golden() -> None:
    golden = json.loads(GOLDEN_EDIT.read_text())
    assert await record_edit() == golden["trace"]


if __name__ == "__main__":
    trace_false = asyncio.run(record(False))
    trace_true = asyncio.run(record(True))
    data = {"False": trace_false, "True": trace_true}
    GOLDEN.write_text(json.dumps(data, indent=2))
    print(f"Golden trace recorded to {GOLDEN}")

    edit_trace = asyncio.run(record_edit())
    edit_data = {"trace": edit_trace}
    GOLDEN_EDIT.write_text(json.dumps(edit_data, indent=2))
    print(f"Golden edit trace recorded to {GOLDEN_EDIT}")
