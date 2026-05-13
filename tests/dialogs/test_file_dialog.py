"""Tests for FileDialog, _FileListing, FileDialogMode, FileTypeFilter."""

from __future__ import annotations

import fnmatch
import pathlib

import pytest
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Input, Static

from nova_navigator.dialogs.file_dialog import (
    FileDialog,
    FileDialogMode,
    FileTypeFilter,
    _FileListing,
)
from nova_widgets import Select

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True, scope="module")
def _load_icons() -> None:
    """Ensure the icon registry is initialised before any test runs."""
    from nova_navigator.icons import ICONS

    if not hasattr(ICONS, "_icons"):
        icons_csv = pathlib.Path(__file__).parent.parent.parent / "src" / "nova_navigator" / "_default" / "icons.csv"
        ICONS.load_icons(icons_csv)


# ── Test apps ──────────────────────────────────────────────────────────────────


class _ListingApp(App[None]):
    """Minimal app that mounts a bare _FileListing, capturing its messages."""

    def __init__(self, listing: _FileListing) -> None:
        super().__init__()
        self._listing = listing
        self.navigated: list[pathlib.Path] = []
        self.confirmed: list[pathlib.Path] = []

    def compose(self) -> ComposeResult:
        yield self._listing

    def on__file_listing_path_navigated(self, event: _FileListing.PathNavigated) -> None:
        self.navigated.append(event.path)

    def on__file_listing_file_confirmed(self, event: _FileListing.FileConfirmed) -> None:
        self.confirmed.append(event.path)


class _DialogApp(App[str]):
    """Minimal app that pushes a FileDialog for layout/interaction tests."""

    def __init__(self, dialog: FileDialog) -> None:
        super().__init__()
        self._dialog = dialog

    def compose(self) -> ComposeResult:
        return iter([])

    async def on_mount(self) -> None:
        await self.push_screen(self._dialog)


class _CapturingApp(App[str]):
    """App that pushes a FileDialog — result captured via dialog.selected_path."""

    def __init__(self, dialog: FileDialog) -> None:
        super().__init__()
        self._dialog = dialog

    def compose(self) -> ComposeResult:
        return iter([])

    async def on_mount(self) -> None:
        await self.push_screen(self._dialog)


# ── Task 1: FileDialogMode and FileTypeFilter ──────────────────────────────────


def test_file_dialog_mode_values() -> None:
    assert FileDialogMode.OPEN.value == "open"
    assert FileDialogMode.SAVE.value == "save"
    assert FileDialogMode.DIR.value == "dir"


def test_file_type_filter_pattern_matching() -> None:
    f = FileTypeFilter(label="Python files", patterns=["*.py", "*.pyi"])
    assert fnmatch.fnmatch("foo.py", f.patterns[0])
    assert not fnmatch.fnmatch("foo.txt", f.patterns[0])


def test_file_type_filter_matches() -> None:
    f = FileTypeFilter(label="Python files", patterns=["*.py", "*.pyi"])
    assert f.matches("script.py")
    assert f.matches("types.pyi")
    assert not f.matches("readme.md")


# ── Task 2: _FileListing rendering ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listing_shows_parent_entry(tmp_path: pathlib.Path) -> None:
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listing._items[0] is None  # None sentinel = ".."


@pytest.mark.asyncio
async def test_listing_dirs_before_files(tmp_path: pathlib.Path) -> None:
    (tmp_path / "aaa").mkdir()
    (tmp_path / "bbb.txt").write_text("x")
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listing._items[1] is not None
        assert listing._items[1].is_dir()
        assert listing._items[2] is not None
        assert listing._items[2].is_file()


@pytest.mark.asyncio
async def test_listing_excludes_dotfiles(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "visible.txt").write_text("y")
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        names = [p.name for p in listing._items if p is not None]
        assert ".hidden" not in names
        assert "visible.txt" in names


# ── Task 3: _FileListing keyboard navigation ───────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_down_moves_cursor(tmp_path: pathlib.Path) -> None:
    (tmp_path / "alpha").mkdir()
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listing._cursor == 0
        await pilot.press("down")
        await pilot.pause()
        assert listing._cursor == 1


@pytest.mark.asyncio
async def test_cursor_up_does_not_go_below_zero(tmp_path: pathlib.Path) -> None:
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listing._cursor == 0
        await pilot.press("up")
        await pilot.pause()
        assert listing._cursor == 0


@pytest.mark.asyncio
async def test_enter_on_dotdot_navigates_to_parent(tmp_path: pathlib.Path) -> None:
    subdir = tmp_path / "sub"
    subdir.mkdir()
    listing = _FileListing(current_path=subdir)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # cursor at 0 = ".."
        await pilot.pause()
        assert app.navigated
        assert app.navigated[0] == tmp_path


@pytest.mark.asyncio
async def test_enter_on_file_emits_file_confirmed(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "report.txt"
    f.write_text("hello")
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # "..": 0, file: 1
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.confirmed
        assert app.confirmed[0] == f


# ── Task 4: FileDialog layout ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dialog_path_bar_shows_start_path(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        path_bar = app.screen.query_one("#path_bar", Static)
        assert str(tmp_path) in str(path_bar.content)


@pytest.mark.asyncio
async def test_dialog_open_mode_input_is_readonly(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#filename_input", Input)
        assert inp.disabled


@pytest.mark.asyncio
async def test_dialog_save_mode_input_is_editable(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.SAVE, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#filename_input", Input)
        assert not inp.disabled


@pytest.mark.asyncio
async def test_dialog_dir_mode_shows_disabled_input(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.DIR, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        row = app.screen.query_one("#filename_row")
        assert row.display
        inp = app.screen.query_one("#filename_input", Input)
        assert inp.disabled


@pytest.mark.asyncio
async def test_dialog_filter_select_shown_when_filters_given(tmp_path: pathlib.Path) -> None:
    filters = [FileTypeFilter("Python", ["*.py"]), FileTypeFilter("All", ["*"])]
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test", filters=filters)
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        sel = app.screen.query_one("#filter_select", Select)
        assert sel is not None


@pytest.mark.asyncio
async def test_dialog_no_filter_select_without_filters(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.screen.query_one("#filter_select", Select)


# ── Task 5: Full round-trip acceptance behavior ───────────────────────────────


def test_start_path_fallback_to_home_when_invalid() -> None:
    bad_path = pathlib.Path("/nonexistent/path/xyz123")
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=bad_path, title="Test")
    assert dialog._current_path == pathlib.Path.home()


def test_start_path_used_when_valid(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test")
    assert dialog._current_path == tmp_path


@pytest.mark.asyncio
async def test_open_mode_ok_accepts_existing_file(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("data")
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        # "..": index 0, file: index 1
        await pilot.press("down")
        await pilot.pause()
        # Enter triggers action_confirm -> FileConfirmed -> sets selected_path & dismisses
        await pilot.press("enter")
        await pilot.pause()
    assert dialog.selected_path == target


@pytest.mark.asyncio
async def test_open_mode_ok_on_nonexistent_file_keeps_dialog_open(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#filename_input", Input)
        # Temporarily enable to set a nonexistent filename
        inp.disabled = False
        inp.value = "does_not_exist.txt"
        await pilot.pause()
        # Tab away to remove listing focus so action_accept_dialog validates
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Dialog must still be present
        assert app.screen.query_one("#filename_input") is not None
        assert dialog.selected_path is None


@pytest.mark.asyncio
async def test_save_mode_ok_returns_composed_path(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.SAVE, start_path=tmp_path, title="Save")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#filename_input", Input)
        await pilot.click(inp)
        for char in "newfile.txt":
            await pilot.press(char)
        await pilot.pause()
        # Tab to move focus off the listing so action_accept_dialog validates
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert dialog.selected_path == tmp_path / "newfile.txt"


@pytest.mark.asyncio
async def test_dir_mode_ok_returns_directory(tmp_path: pathlib.Path) -> None:
    subdir = tmp_path / "mydir"
    subdir.mkdir()
    dialog = FileDialog(mode=FileDialogMode.DIR, start_path=tmp_path, title="Dir")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Move cursor to subdir (index 1), then call validate directly
        await pilot.press("down")
        await pilot.pause()
        # Directly call _validate_and_store to simulate OK in DIR mode
        result = dialog._validate_and_store()
        assert result
    assert dialog.selected_path == subdir


@pytest.mark.asyncio
async def test_cancel_returns_cancel_button_id(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert dialog.selected_path is None


@pytest.mark.asyncio
async def test_permission_denied_directory_stays_put(tmp_path: pathlib.Path) -> None:
    restricted = tmp_path / "restricted"
    restricted.mkdir(mode=0o000)
    try:
        listing = _FileListing(current_path=tmp_path)
        app = _ListingApp(listing)
        async with app.run_test() as pilot:
            await pilot.pause()
            with pytest.raises((PermissionError, OSError)):
                listing.navigate_to(restricted)
            await pilot.pause()
            assert listing._current_path == tmp_path
    finally:
        restricted.chmod(0o755)


@pytest.mark.asyncio
async def test_filter_hides_non_matching_files(tmp_path: pathlib.Path) -> None:
    (tmp_path / "script.py").write_text("")
    (tmp_path / "readme.md").write_text("")
    py_filter = FileTypeFilter("Python", ["*.py"])
    listing = _FileListing(current_path=tmp_path, active_filter=py_filter)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        names = [p.name for p in listing._items if p is not None]
        assert "script.py" in names
        assert "readme.md" not in names


@pytest.mark.asyncio
async def test_cursor_updates_filename_input_in_open_mode(tmp_path: pathlib.Path) -> None:
    (tmp_path / "report.txt").write_text("data")
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # move cursor to the file
        await pilot.pause()
        inp = app.screen.query_one("#filename_input", Input)
        assert inp.value == "report.txt"


@pytest.mark.asyncio
async def test_navigation_updates_path_bar(tmp_path: pathlib.Path) -> None:
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "file.txt").write_text("")
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        # cursor at "..": 0, then move to subdir: 1
        await pilot.press("down")
        await pilot.pause()
        # Enter on directory navigates into it
        listing = app.screen.query_one("#listing", _FileListing)
        listing.action_confirm()
        await pilot.pause()
        path_bar = app.screen.query_one("#path_bar", Static)
        assert str(subdir) in str(path_bar.content)
