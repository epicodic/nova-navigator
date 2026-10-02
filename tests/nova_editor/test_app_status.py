"""The status line of nova_edit: pure formatting, coalescing, and the live fields (REQ-6, REQ-16, REQ-17)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.app import NovaEditApp
from nova_editor.status_line import StatusLine, StatusState, format_status
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import wait_saved


def state(**changes: object) -> StatusState:
    base = StatusState(
        line=12_345,
        column=17,
        column_kind="exact",
        byte_offset=5_678_901,
        line_count=46_944_995,
        line_count_exact=True,
        indexing_percent=100,
        line_ending="LF",
        modified=False,
        new_file=False,
        wrap=False,
        goto_percent=None,
    )
    return dataclasses.replace(base, **changes)


def test_the_full_line() -> None:
    assert format_status(state(), 200) == "Ln 12,345  Col 17  Byte 5,678,901  46,944,995 lines  LF  No wrap"


def test_while_indexing_the_count_is_a_lower_bound_with_the_percentage() -> None:
    text = format_status(state(line_count=1_234_567, line_count_exact=False, indexing_percent=37), 200)
    assert ">= 1,234,567 lines (indexing 37%)" in text


def test_a_single_line_is_singular() -> None:
    assert "1 line  " in format_status(state(line_count=1), 200)


@pytest.mark.parametrize(("kind", "shown"), [("exact", "Col 17"), ("provisional", "Col ~17"), ("pending", "Col ...")])
def test_the_column_shows_how_exact_it_is(kind: str, shown: str) -> None:
    assert format_status(state(column_kind=kind), 200).startswith(f"Ln 12,345  {shown}  ")


def test_an_unknown_byte_offset_is_a_question_mark() -> None:
    assert "Byte ?" in format_status(state(byte_offset=None), 200)


def test_modified_new_file_wrap_and_line_ending_are_shown() -> None:
    text = format_status(state(modified=True, new_file=True, wrap=True, line_ending="CRLF"), 200)
    assert text.endswith("CRLF  Modified  New file  Wrap")


def test_the_tail_is_cut_first_on_a_narrow_terminal() -> None:
    assert format_status(state(modified=True), 30) == "Ln 12,345  Col 17"
    assert format_status(state(modified=True), 60).startswith("Ln 12,345  Col 17  Byte 5,678,901  46,944,995 lines")


def test_a_goto_in_progress_comes_first() -> None:
    assert format_status(state(goto_percent=37), 200).startswith("Goto 37%  Esc cancels  Ln 12,345")


class StatusHost(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.value = state()

    def read(self) -> StatusState:
        self.reads += 1
        return self.value

    def compose(self) -> ComposeResult:
        yield StatusLine(self.read)


@pytest.mark.asyncio
async def test_requests_are_coalesced_and_the_last_state_wins() -> None:
    host = StatusHost()
    async with host.run_test() as pilot:
        await pilot.pause(0.2)
        status = host.query_one(StatusLine)
        before = host.reads
        for line in range(1, 101):
            host.value = state(line=line)
            status.request()
        await wait_until(pilot, lambda: status.text.startswith("Ln 100"))
        assert host.reads - before <= 2
        assert status.flushes >= 1


def make_file(tmp_path: Path, data: bytes = b"one\ntwo\nthree\n") -> Path:
    path = tmp_path / "f.txt"
    path.write_bytes(data)
    return path


def status_text(app: NovaEditApp) -> str:
    return app.query_one(StatusLine).text


@pytest.mark.asyncio
async def test_the_status_line_follows_the_cursor(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: status_text(app).startswith("Ln 1  Col 1  Byte 0  "))
        assert app.editor is not None
        assert f"{app.editor.line_count} lines" in status_text(app)
        assert "LF" in status_text(app)
        assert status_text(app).endswith("No wrap")
        await pilot.press("down", "right")
        await wait_until(pilot, lambda: status_text(app).startswith("Ln 2  Col 2  Byte 5  "))


@pytest.mark.asyncio
async def test_modified_appears_on_typing_and_goes_after_the_save(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await wait_until(pilot, lambda: "Modified" in status_text(app))
        await pilot.press("ctrl+s")
        assert app.editor is not None
        await wait_saved(pilot, app.editor)
        await wait_until(pilot, lambda: "Modified" not in status_text(app))


@pytest.mark.asyncio
@pytest.mark.parametrize(("data", "ending"), [(b"a\r\nb\r\n", "CRLF"), (b"a\rb\r", "CR"), (b"a\nb\n", "LF")])
async def test_the_line_ending_is_shown(tmp_path: Path, data: bytes, ending: str) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path, data))
    async with app.run_test() as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: f"  {ending}  " in status_text(app))


@pytest.mark.asyncio
async def test_f4_toggles_the_wrap_text(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f4")
        await wait_until(pilot, lambda: status_text(app).endswith("  Wrap"))
        await pilot.press("f4")
        await wait_until(pilot, lambda: status_text(app).endswith("No wrap"))


@pytest.mark.asyncio
async def test_a_missing_path_shows_new_file_until_it_is_saved(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=tmp_path / "new.txt")
    async with app.run_test() as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: "New file" in status_text(app))
        await pilot.press("h", "i", "ctrl+s")
        assert app.editor is not None
        await wait_saved(pilot, app.editor)
        await wait_until(pilot, lambda: "New file" not in status_text(app))


@pytest.mark.asyncio
async def test_a_pending_goto_shows_its_progress(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        app.editor.pending_progress = 0.5
        await wait_until(pilot, lambda: status_text(app).startswith("Goto 50%  Esc cancels"))
        app.editor.pending_progress = None
        await wait_until(pilot, lambda: not status_text(app).startswith("Goto"))
