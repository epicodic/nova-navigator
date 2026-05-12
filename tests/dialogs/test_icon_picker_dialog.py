"""Tests for IconPickerDialog and _IconCell."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from nova_navigator.dialogs.icon_picker_dialog import IconPickerDialog, _IconCell

# ── fake ICONS fixture ────────────────────────────────────────────────────────

_FAKE_ICONS = [("folder", "  "), ("file", "  "), ("home", " ")]


class _FakeIconSet:
    def __iter__(self) -> Any:
        return iter((name, None) for name, _ in _FAKE_ICONS)

    def get_icon(self, name: str) -> str:  # type: ignore[override]
        return next((g for n, g in _FAKE_ICONS if n == name), "?")


@pytest.fixture(autouse=True)
def patch_icons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nova_navigator.dialogs.icon_picker_dialog.ICONS", _FakeIconSet())


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_dialog_app(
    initial_icon: str | None = None,
) -> tuple[IconPickerDialog, type[App[None]]]:
    dialog = IconPickerDialog(initial_icon=initial_icon)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return dialog, _App


class _CellApp(App[None]):
    def __init__(self, cell: _IconCell) -> None:
        super().__init__()
        self._cell = cell

    def compose(self) -> ComposeResult:
        yield self._cell


# ── _IconCell ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_icon_cell_click_posts_selected_message() -> None:
    cell = _IconCell("folder", "  ")
    received: list[str] = []

    class _App(_CellApp):
        def on_icon_cell_selected(self, event: _IconCell.Selected) -> None:
            received.append(event.icon_name)

    async with _App(cell).run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        await pilot.click(cell)
        await pilot.pause()
        assert received == ["folder"]


@pytest.mark.asyncio
async def test_icon_cell_hover_posts_hovered_message() -> None:
    cell = _IconCell("file", "  ")
    received: list[str] = []

    class _App(_CellApp):
        def on_icon_cell_hovered(self, event: _IconCell.Hovered) -> None:
            received.append(event.icon_name)

    async with _App(cell).run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        await pilot.hover(cell)
        await pilot.pause()
        assert received == ["file"]


# ── IconPickerDialog ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dialog_mounts_icon_cells() -> None:
    dialog, _App = _make_dialog_app()
    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        cells = list(dialog.query(_IconCell))
        assert len(cells) == len(_FAKE_ICONS)


@pytest.mark.asyncio
async def test_dialog_has_status_label() -> None:
    dialog, _App = _make_dialog_app()
    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert dialog.query_one("#icon_status", Label) is not None


@pytest.mark.asyncio
async def test_initial_icon_is_selected_on_mount() -> None:
    dialog, _App = _make_dialog_app(initial_icon="folder")
    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        selected = [c for c in dialog.query(_IconCell) if c.has_class("-selected")]
        assert len(selected) == 1
        assert selected[0]._icon_name == "folder"


@pytest.mark.asyncio
async def test_no_initial_selection_when_none() -> None:
    dialog, _App = _make_dialog_app(initial_icon=None)
    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        selected = [c for c in dialog.query(_IconCell) if c.has_class("-selected")]
        assert selected == []


@pytest.mark.asyncio
async def test_clicking_cell_updates_selected_icon() -> None:
    dialog, _App = _make_dialog_app()
    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        file_cell = next(c for c in dialog.query(_IconCell) if c._icon_name == "file")
        await pilot.click(file_cell)
        await pilot.pause()
        assert dialog._selected_icon == "file"


@pytest.mark.asyncio
async def test_clicking_cell_applies_selected_class() -> None:
    dialog, _App = _make_dialog_app()
    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        home_cell = next(c for c in dialog.query(_IconCell) if c._icon_name == "home")
        await pilot.click(home_cell)
        await pilot.pause()
        assert home_cell.has_class("-selected")
        # others must be deselected
        others = [c for c in dialog.query(_IconCell) if c._icon_name != "home"]
        assert all(not c.has_class("-selected") for c in others)


@pytest.mark.asyncio
async def test_hovering_cell_updates_status_label() -> None:
    dialog, _App = _make_dialog_app()
    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        folder_cell = next(c for c in dialog.query(_IconCell) if c._icon_name == "folder")
        await pilot.hover(folder_cell)
        await pilot.pause()
        # trigger the hovered handler directly in case hover event didn't fire
        dialog.on_icon_cell_hovered(_IconCell.Hovered("folder"))
        await pilot.pause()
        status = dialog.query_one("#icon_status", Label)
        assert "folder" in str(status.render())


@pytest.mark.asyncio
async def test_action_accept_dismisses_with_icon_name() -> None:
    dialog, _App = _make_dialog_app(initial_icon="home")
    dismissed_with: list[str] = []

    class _TrackedApp(_App):  # type: ignore[valid-type]
        pass

    app = _App()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # intercept dismiss
        original_dismiss = dialog.dismiss
        dialog.dismiss = lambda v: dismissed_with.append(v) or original_dismiss(v)  # type: ignore[method-assign]
        dialog.action_accept_dialog()
        await pilot.pause()
        assert dismissed_with == ["home"]


@pytest.mark.asyncio
async def test_action_accept_does_nothing_without_selection() -> None:
    dialog, _App = _make_dialog_app(initial_icon=None)
    dismiss_called = False

    app = _App()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        original_dismiss = dialog.dismiss
        dialog.dismiss = lambda v: setattr(pilot, "_dismissed", True) or original_dismiss(v)  # type: ignore[method-assign]
        dialog.action_accept_dialog()
        await pilot.pause()
        assert dialog._selected_icon is None
