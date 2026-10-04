"""The status line of nova_edit: pure formatting, coalescing, and the live fields (REQ-6, REQ-16, REQ-17)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.app import NovaEditApp
from nova_editor.status_line import StatusLine, StatusState, byte_percent, format_status, row_is_known
from tests.nova_editor.helpers_view import GatedRowEditor, GatedRowsEditor, wait_until
from tests.nova_editor.save_widget_helpers import wait_saved


def state(**changes: object) -> StatusState:
    base = StatusState(
        file_name="notes.txt",
        line=12_345,
        column=17,
        column_kind="exact",
        byte_offset=5_678_901,
        byte_percent=12,
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
    assert format_status(state(), 200) == "notes.txt  Ln 12,345  Col 17  Byte 5,678,901 (12%)  No wrap  46,944,995 lines  LF"


def test_while_indexing_the_count_is_a_lower_bound_with_the_percentage() -> None:
    text = format_status(state(line_count=1_234_567, line_count_exact=False, indexing_percent=37), 200)
    assert ">= 1,234,567 lines (indexing 37%)" in text


def test_a_single_line_is_singular() -> None:
    assert "1 line  " in format_status(state(line_count=1), 200)


@pytest.mark.parametrize(("kind", "shown"), [("exact", "Col 17"), ("provisional", "Col ~17"), ("pending", "Col ...")])
def test_the_column_shows_how_exact_it_is(kind: str, shown: str) -> None:
    assert format_status(state(column_kind=kind), 200).startswith(f"notes.txt  Ln 12,345  {shown}  ")


def test_an_unknown_byte_offset_is_a_question_mark() -> None:
    assert "Byte ?" in format_status(state(byte_offset=None), 200)


def test_modified_new_file_wrap_and_line_ending_are_shown() -> None:
    text = format_status(state(modified=True, new_file=True, wrap=True, line_ending="CRLF"), 200)
    assert text == "notes.txt  Ln 12,345  Col 17  Byte 5,678,901 (12%)  Modified  New file  Wrap  46,944,995 lines  CRLF"


def test_the_tail_is_cut_first_on_a_narrow_terminal() -> None:
    assert format_status(state(modified=True), 30) == "notes.txt  Ln 12,345  Col 17"
    assert format_status(state(modified=True), 60) == "notes.txt  Ln 12,345  Col 17  Byte 5,678,901 (12%)  Modified"


def test_a_goto_in_progress_comes_first() -> None:
    assert format_status(state(goto_percent=37), 200).startswith("notes.txt  Goto 37%  Esc cancels  Ln 12,345")


def test_an_unknown_row_is_a_question_mark_and_column_and_byte_stay_shown() -> None:
    text = format_status(state(line=None), 200)
    assert text.startswith("notes.txt  Ln ?  Col 17  Byte 5,678,901 (12%)  ")


def test_an_unknown_row_with_an_unknown_byte_shows_both_question_marks() -> None:
    assert format_status(state(line=None, byte_offset=None, byte_percent=None), 200).startswith("notes.txt  Ln ?  Col 17  Byte ?  ")


def test_the_file_name_leads_and_a_document_without_a_path_says_so() -> None:
    assert format_status(state(), 200).startswith("notes.txt  ")
    assert format_status(state(file_name=None), 200).startswith("(unsaved)  ")


def test_the_byte_percent_is_left_out_when_it_is_unknown() -> None:
    text = format_status(state(byte_percent=None), 200)
    assert "Byte 5,678,901  No wrap" in text
    assert "%" not in text.split("Byte")[1].split("No wrap")[0]


def test_a_long_file_name_keeps_its_end_behind_an_ellipsis() -> None:
    name = "abcdefghijklmnopqrstuvwxyz.txt"
    text = format_status(state(file_name=name), 20)
    assert text == "…vwxyz.txt"
    assert len(text) <= 20 // 2
    assert format_status(state(file_name="a" * 10), 20) == "a" * 10  # exactly half of the width is kept whole


def test_a_one_cell_width_shows_the_name_as_an_ellipsis() -> None:
    assert format_status(state(file_name="ab"), 1) == "…"


def test_modified_and_the_wrap_indicator_survive_80_columns_with_big_numbers() -> None:
    text = format_status(state(modified=True), 80)
    assert text == "notes.txt  Ln 12,345  Col 17  Byte 5,678,901 (12%)  Modified  No wrap"
    assert len(text) <= 80


@pytest.mark.parametrize(("wrap", "shown"), [(False, "No wrap"), (True, "Wrap")])
def test_each_of_the_two_wrap_modes_has_its_own_indicator(wrap: bool, shown: str) -> None:
    assert f"  {shown}  " in format_status(state(wrap=wrap), 200)


@pytest.mark.parametrize(
    ("offset", "length", "expected"),
    [(0, 100, 0), (5, 14, 35), (99, 100, 99), (100, 100, 100), (None, 100, None), (5, 0, None), (0, 0, None)],
)
def test_byte_percent(offset: int | None, length: int, expected: int | None) -> None:
    assert byte_percent(offset, length) == expected


@pytest.mark.parametrize(
    ("row", "count", "complete", "known"),
    [(0, 1, False, False), (0, 2, False, True), (268, 270, False, True), (269, 270, False, False), (269, 270, True, True), (0, 1, True, True)],
)
def test_row_is_known(row: int, count: int, complete: bool, known: bool) -> None:
    assert row_is_known(row, count, complete) is known


class StatusHost(App[None]):
    CSS = "StatusLine { width: 100%; height: 1; }"

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
        await wait_until(pilot, lambda: status.text.startswith("notes.txt  Ln 100"))
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
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Ln 1  Col 1  Byte 0 (0%)  No wrap  "))
        assert app.editor is not None
        assert f"{app.editor.line_count} lines" in status_text(app)
        assert status_text(app).endswith("  LF")
        await pilot.press("down", "right")
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Ln 2  Col 2  Byte 5 (35%)  "))


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
        await wait_until(pilot, lambda: status_text(app).endswith(f"  {ending}"))


@pytest.mark.asyncio
async def test_f10_toggles_the_wrap_text(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f10")
        await wait_until(pilot, lambda: "  Wrap  " in status_text(app))
        await pilot.press("f10")
        await wait_until(pilot, lambda: "  No wrap  " in status_text(app))


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
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Goto 50%  Esc cancels"))
        app.editor.pending_progress = None
        await wait_until(pilot, lambda: "Goto" not in status_text(app))


@pytest.mark.asyncio
async def test_the_row_and_the_byte_are_unknown_until_the_scan_resolves_the_row(tmp_path: Path) -> None:
    path = make_file(tmp_path, b"x" * 5000)  # one row, no line ending
    GatedRowEditor.gates.clear()
    app = NovaEditApp(file_path=path, editor_class=GatedRowEditor)
    async with app.run_test() as pilot:
        await pilot.pause()
        gate = GatedRowEditor.gates[0]
        await wait_until(pilot, gate.blocked.is_set)
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Ln ?  Col 1  Byte ?  "))
        assert app.return_code is None
        gate.release()
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Ln 1  Col 1  Byte 0 (0%)  "))


@pytest.mark.asyncio
async def test_a_resize_refits_the_text() -> None:
    host = StatusHost()
    async with host.run_test(size=(100, 10)) as pilot:
        status = host.query_one(StatusLine)
        await wait_until(pilot, lambda: status.text.endswith("  LF"))
        await pilot.resize_terminal(30, 10)
        await wait_until(pilot, lambda: status.text == "notes.txt  Ln 12,345  Col 17")
        await pilot.resize_terminal(100, 10)
        await wait_until(pilot, lambda: status.text.endswith("  LF"))


@pytest.mark.asyncio
async def test_the_row_is_shown_while_the_index_is_incomplete_when_its_row_is_resolved(tmp_path: Path) -> None:
    path = make_file(tmp_path, "".join(f"row {i}\n" for i in range(3000)).encode())
    GatedRowEditor.gates.clear()
    app = NovaEditApp(file_path=path, editor_class=GatedRowsEditor)
    async with app.run_test() as pilot:
        await pilot.pause()
        gate = GatedRowEditor.gates[0]
        await wait_until(pilot, gate.blocked.is_set)  # the scan is held at 2 KiB: the count is a lower bound
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Ln 1  Col 1  Byte 0 (0%)  No wrap  >= "))
        assert "(indexing " in status_text(app)
        gate.release()
        await wait_until(pilot, lambda: status_text(app).endswith("3,001 lines  LF"))


@pytest.mark.asyncio
async def test_the_byte_percent_follows_the_cursor_to_the_end(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))  # 14 bytes
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "down", "down")
        await wait_until(pilot, lambda: "Ln 4  Col 1  Byte 14 (100%)" in status_text(app))
        await pilot.press("up")
        await wait_until(pilot, lambda: "Ln 3  Col 1  Byte 8 (57%)" in status_text(app))


@pytest.mark.asyncio
async def test_a_buffer_without_a_path_shows_unsaved_and_no_percent_of_an_empty_file() -> None:
    app = NovaEditApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: status_text(app).startswith("(unsaved)  Ln 1  Col 1  Byte 0  "))
