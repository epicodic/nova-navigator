"""Golden trace of the stock (`text=`) path: cursor, scroll and rendered lines after scripted keys (recorded on BASE 2a7bb25)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.widget import NovaTextArea

GOLDEN = Path(__file__).parent / "golden" / "stock_trace.json"
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


class TraceApp(App[None]):
    def __init__(self, *, soft_wrap: bool) -> None:
        super().__init__()
        self._soft_wrap = soft_wrap

    def compose(self) -> ComposeResult:
        yield NovaTextArea(TEXT, soft_wrap=self._soft_wrap, id="ta")


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


@pytest.mark.asyncio
@pytest.mark.parametrize("soft_wrap", [False, True])
async def test_stock_trace_matches_golden(soft_wrap: bool) -> None:
    golden = json.loads(GOLDEN.read_text())
    assert await record(soft_wrap) == golden[str(soft_wrap)]


if __name__ == "__main__":
    trace_false = asyncio.run(record(False))
    trace_true = asyncio.run(record(True))
    data = {"False": trace_false, "True": trace_true}
    GOLDEN.write_text(json.dumps(data, indent=2))
    print(f"Golden trace recorded to {GOLDEN}")
