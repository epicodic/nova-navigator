from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Collapsible, Input, Static

from nova_navigator.dialogs.files_dialog import CopyMoveFilesDialog, DeleteFilesDialog
from nova_navigator.file_filter import FilenamePatternFilter
from nova_navigator.vfs.filesystem import VPath
from nova_navigator.vfs.types import Stat


def _make_vpath(name: str, compact: str = "/some/path") -> MagicMock:
    vp = MagicMock(spec=VPath)
    vp.name = name
    vp.compact_path_str = compact
    return vp


def _make_copy_app(
    sources: list[MagicMock],
    dest: MagicMock,
    move: bool = False,
) -> tuple[CopyMoveFilesDialog, type[App[None]]]:
    dialog = CopyMoveFilesDialog(sources, dest, move=move)  # type: ignore

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return dialog, _App


def _make_delete_app(
    paths: list[MagicMock] | list[VPath],
) -> tuple[DeleteFilesDialog, type[App[None]]]:
    dialog = DeleteFilesDialog(paths)  # type: ignore

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return dialog, _App


# ── CopyMoveFilesDialog ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_copy_dialog_title_is_copy() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst, move=False)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert dialog._title == "Copy"


@pytest.mark.asyncio
async def test_move_dialog_title_is_move() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst, move=True)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert dialog._title == "Move"


@pytest.mark.asyncio
async def test_copy_dialog_single_file_shows_input() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        inp = dialog.query_one("#filename_input", Input)
        assert inp.value == "file.txt"


@pytest.mark.asyncio
async def test_copy_dialog_multiple_files_no_input() -> None:
    src = [_make_vpath("a.txt"), _make_vpath("b.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # No filename Input; the only Input is the filter_pattern one inside the Collapsible
        inputs = list(dialog.query(Input))
        assert all(inp.id == "filter_pattern" for inp in inputs)


@pytest.mark.asyncio
async def test_copy_dialog_shows_destination() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/some/destination")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # verify the destination Static widget is present
        assert dialog.query_one("#destination", Static) is not None


@pytest.mark.asyncio
async def test_copy_dialog_source_files_listed() -> None:
    src = [_make_vpath("alpha.txt"), _make_vpath("beta.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        items = list(dialog._source_files.query("ListItem"))
        assert len(items) == 2


@pytest.mark.asyncio
async def test_copy_dialog_truncates_long_source_list() -> None:
    src = [_make_vpath(f"file{i}.txt") for i in range(15)]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        items = list(dialog._source_files.query("ListItem"))
        # MAX_DISPLAYED_FILES (10) items + 1 "... and N more" item
        assert len(items) == CopyMoveFilesDialog.MAX_DISPLAYED_FILES + 1


@pytest.mark.asyncio
async def test_capture_filename_stores_input_value() -> None:
    src = [_make_vpath("original.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        dialog.query_one("#filename_input", Input).value = "renamed.txt"
        dialog._capture_filename()
        assert dialog.filename == "renamed.txt"


@pytest.mark.asyncio
async def test_capture_filename_falls_back_to_original_when_empty() -> None:
    src = [_make_vpath("original.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        dialog.query_one("#filename_input", Input).value = "   "
        dialog._capture_filename()
        assert dialog.filename == "original.txt"


@pytest.mark.asyncio
async def test_capture_filename_noop_for_multiple_sources() -> None:
    src = [_make_vpath("a.txt"), _make_vpath("b.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        dialog._capture_filename()  # should not raise
        assert dialog.filename is None


@pytest.mark.asyncio
async def test_action_accept_captures_filename() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        dialog.query_one("#filename_input", Input).value = "newname.txt"
        await pilot.press("enter")
        await pilot.pause()
        assert dialog.filename == "newname.txt"


@pytest.mark.asyncio
async def test_ok_button_captures_filename() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        dialog.query_one("#filename_input", Input).value = "via_button.txt"
        ok_btn = dialog.query_one("#OK")
        await pilot.click(ok_btn)
        await pilot.pause()
        assert dialog.filename == "via_button.txt"


# ── DeleteFilesDialog ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_dialog_single_file_message() -> None:
    vp = _make_vpath("report.pdf")
    dialog, _App = _make_delete_app([vp])
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # verify dialog mounts with YES/NO buttons
        assert dialog.query_one("#YES") is not None
        assert dialog.query_one("#NO") is not None


@pytest.mark.asyncio
async def test_filter_section_collapsed_by_default() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        collapsible = dialog.query_one(Collapsible)
        assert collapsible.collapsed is True


@pytest.mark.asyncio
async def test_filter_file_filter_returns_none_for_wildcard() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        collapsible = dialog.query_one(Collapsible)
        collapsible.collapsed = False  # expand so the collapsed guard doesn't fire
        await pilot.pause()
        # default value is "*" — should still return None
        assert dialog.file_filter is None


@pytest.mark.asyncio
async def test_filter_file_filter_returns_filter_for_pattern() -> None:
    src = [_make_vpath("file.txt")]
    dst = _make_vpath("dest", "/dest")
    dialog, _App = _make_copy_app(src, dst)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Expand the collapsible and set a non-wildcard pattern
        collapsible = dialog.query_one(Collapsible)
        collapsible.collapsed = False
        await pilot.pause()
        filter_input = dialog.query_one("#filter_pattern", Input)
        filter_input.value = "*.txt"
        result = dialog.file_filter
        assert isinstance(result, FilenamePatternFilter)
        assert result.patterns == ["*.txt"]


@pytest.mark.asyncio
async def test_delete_dialog_multiple_files_message() -> None:
    paths = [_make_vpath(f"f{i}.txt") for i in range(3)]
    dialog, _App = _make_delete_app(paths)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # compose_content uses len(paths) — just verify dialog mounts cleanly
        assert dialog.query_one("#YES") is not None


def _make_link_vpath(name: str, *, is_symlink: bool = True) -> MagicMock:
    vp = _make_vpath(name)
    vp.__str__.return_value = f"/some/path/{name}"
    vp.cached_stat = Stat(is_symlink=is_symlink, is_directory=True)
    return vp


def _texts(dialog: DeleteFilesDialog) -> list[str]:
    return [str(s.content) for s in dialog.query(Static)]


@pytest.mark.asyncio
async def test_delete_dialog_link_wording() -> None:
    dialog, _App = _make_delete_app([_make_link_vpath("lnk")])
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        texts = _texts(dialog)
        assert any(t.startswith("Delete link '") and "lnk" in t for t in texts)
        assert any("Only the link is deleted" in t for t in texts)


@pytest.mark.asyncio
async def test_delete_dialog_regular_file_unchanged() -> None:
    dialog, _App = _make_delete_app([_make_link_vpath("f", is_symlink=False)])
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        texts = _texts(dialog)
        assert "Delete /some/path/f?" in texts
        assert not any("link" in t for t in texts)


@pytest.mark.asyncio
async def test_delete_dialog_mixed_selection_mentions_links() -> None:
    paths = [_make_link_vpath("a"), _make_link_vpath("b"), _make_link_vpath("c", is_symlink=False)]
    dialog, _App = _make_delete_app(paths)
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        texts = _texts(dialog)
        assert "Delete 3 files?" in texts
        assert "2 of them are links; only the links are deleted, not what they point to." in texts


@pytest.mark.asyncio
async def test_delete_dialog_multiple_no_links_unchanged() -> None:
    dialog, _App = _make_delete_app([_make_vpath("a"), _make_vpath("b")])
    async with _App().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        texts = _texts(dialog)
        assert "Delete 2 files?" in texts
        assert not any("links" in t for t in texts)


@pytest.mark.asyncio
async def test_delete_dialog_does_no_filesystem_io() -> None:
    fs = MagicMock()
    fs.stat = MagicMock(return_value=Stat())
    cached_link = VPath("/x/cached", fs)
    cached_link._stat = Stat(is_symlink=True, is_directory=True)
    uncached = VPath("/x/uncached", fs)

    link_dialog, link_app = _make_delete_app([cached_link])
    async with link_app().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert any(t.startswith("Delete link '") for t in _texts(link_dialog))

    plain_dialog, plain_app = _make_delete_app([uncached])
    async with plain_app().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        texts = _texts(plain_dialog)
        assert not any("link" in t for t in texts)
        assert any(t.startswith("Delete ") for t in texts)

    fs.stat.assert_not_called()
