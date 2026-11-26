import pytest
from textual.app import App, ComposeResult

from nova_navigator.dialogs.select_by_pattern_dialog import SelectByPatternDialog
from nova_widgets import Checkbox, Input, Response


def _make_app(dialog: SelectByPatternDialog, results: list[Response | None]) -> type[App[None]]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog, results.append)

    return _App


@pytest.mark.asyncio
async def test_select_dialog_title() -> None:
    dialog = SelectByPatternDialog(select=True)
    async with _make_app(dialog, [])().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert dialog._title == "Select By Pattern"


@pytest.mark.asyncio
async def test_unselect_dialog_title() -> None:
    dialog = SelectByPatternDialog(select=False)
    async with _make_app(dialog, [])().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert dialog._title == "Unselect By Pattern"


@pytest.mark.asyncio
async def test_pattern_input_prefilled_with_star() -> None:
    dialog = SelectByPatternDialog(select=True)
    async with _make_app(dialog, [])().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pattern_input = dialog.query_one("#pattern_input", Input)
        assert pattern_input.value == "*"
        assert dialog.focused is pattern_input


@pytest.mark.asyncio
async def test_include_folders_checkbox_off_by_default() -> None:
    dialog = SelectByPatternDialog(select=True)
    async with _make_app(dialog, [])().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert dialog.query_one("#include_folders", Checkbox).value is False


@pytest.mark.asyncio
async def test_include_folders_checkbox_initial_true() -> None:
    dialog = SelectByPatternDialog(select=True, include_folders=True)
    async with _make_app(dialog, [])().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert dialog.query_one("#include_folders", Checkbox).value is True


@pytest.mark.asyncio
async def test_ok_captures_pattern_and_include_folders() -> None:
    results: list[Response | None] = []
    dialog = SelectByPatternDialog(select=True)
    async with _make_app(dialog, results)().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        dialog.query_one("#pattern_input", Input).value = "*.py;*.txt"
        await pilot.click(dialog.query_one("#include_folders", Checkbox))
        await pilot.press("enter")
        await pilot.pause()
        assert results == [Response.OK]
        assert dialog.pattern == "*.py;*.txt"
        assert dialog.include_folders is True


@pytest.mark.asyncio
async def test_enter_in_pattern_input_accepts() -> None:
    results: list[Response | None] = []
    dialog = SelectByPatternDialog(select=True)
    async with _make_app(dialog, results)().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert results == [Response.OK]
        assert dialog.pattern == "*"


@pytest.mark.asyncio
async def test_escape_cancels() -> None:
    results: list[Response | None] = []
    dialog = SelectByPatternDialog(select=True)
    async with _make_app(dialog, results)().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert results == [Response.CANCEL]


@pytest.mark.asyncio
async def test_empty_pattern_matches_everything() -> None:
    dialog = SelectByPatternDialog(select=True)
    async with _make_app(dialog, [])().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        dialog.query_one("#pattern_input", Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
        assert dialog.file_filter.patterns == ["*"]


@pytest.mark.asyncio
async def test_file_filter_splits_on_semicolon() -> None:
    dialog = SelectByPatternDialog(select=True, pattern=" *.py ; *.txt ")
    assert dialog.file_filter.patterns == ["*.py", "*.txt"]
