"""A short row is cropped to the viewport only, never to the (still growing) estimated virtual width of the lazy document."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.geometry import Size

from nova_editor.app import NovaEditApp
from nova_editor.widget import NovaTextArea


@pytest.mark.asyncio
async def test_row_wider_than_estimated_virtual_width_is_fully_rendered(tmp_path: Path) -> None:
    path = tmp_path / "rows.txt"
    path.write_text("short\n" + "x" * 100 + " END\n")
    app = NovaEditApp(path=path)
    async with app.run_test(size=(160, 20)) as pilot:
        await pilot.pause(0.5)
        area = app.screen.query_one(NovaTextArea)
        area.virtual_size = Size(20, 2)  # the width estimate while the file is still being scanned
        area._line_cache.clear()
        assert area.render_line(1).text.rstrip().endswith("x END")
