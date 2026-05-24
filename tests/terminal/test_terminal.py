"""Unit tests for the Terminal widget and related utilities in terminal/terminal.py."""

from __future__ import annotations

import asyncio
import contextlib
from io import StringIO
from pathlib import PurePath
from typing import Any

import pytest
from pyte.screens import Char
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.geometry import Size

from nova_navigator.terminal.pty_backend import PtyBackend
from nova_navigator.terminal.shell_driver import FallbackDriver, ZshDriver
from nova_navigator.terminal.terminal import (
    Terminal,
    TerminalDisplay,
    TerminalPyteScreen,
    _encode_mouse,
    _translate_terminal_color,
)


class FakePtyBackend(PtyBackend):
    """Test double for PtyBackend that records calls without forking a process."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[bytes] = []
        self.resume_count: int = 0
        self.opened: bool = False
        self.torn_down: bool = False
        self.resize_calls: list[tuple[int, int]] = []
        self._attached: bool = False

    def open(self, command: str, rows: int, cols: int) -> int | None:
        self.opened = True
        return None

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))

    def resume(self) -> None:
        self.resume_count += 1

    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        self._attached = True

    def detach_readers(self) -> None:
        self._attached = False

    def teardown(self) -> None:
        self.torn_down = True


# ---------------------------------------------------------------------------
# Minimal Textual test app that hosts a Terminal widget
# ---------------------------------------------------------------------------


class TerminalTestApp(App[None]):
    def __init__(self, terminal: Terminal) -> None:
        super().__init__()
        self._terminal = terminal

    def compose(self) -> ComposeResult:
        yield self._terminal


# ---------------------------------------------------------------------------
# _translate_terminal_color
# ---------------------------------------------------------------------------


def test_translate_terminal_color_lowercase_hex_gets_hash_prefix() -> None:
    assert _translate_terminal_color("ff0000") == "#ff0000"


def test_translate_terminal_color_uppercase_hex_gets_hash_prefix() -> None:
    assert _translate_terminal_color("AABBCC") == "#AABBCC"


def test_translate_terminal_color_named_color_red_maps_to_hex() -> None:
    result = _translate_terminal_color("red")
    assert result.startswith("#")


def test_translate_terminal_color_named_color_black_maps_to_hex() -> None:
    assert _translate_terminal_color("black") == "#000000"


def test_translate_terminal_color_default_passes_through() -> None:
    assert _translate_terminal_color("default") == "default"


def test_translate_terminal_color_unknown_string_passes_through() -> None:
    assert _translate_terminal_color("xyzunknown") == "xyzunknown"


# ---------------------------------------------------------------------------
# _encode_mouse
# ---------------------------------------------------------------------------


def test_encode_mouse_click_button1_encodes_sgr_press_and_release() -> None:
    result = _encode_mouse(["click", 4, 2, 1])
    assert b"\x1b[<0;" in result
    assert result.endswith(b"m")


def test_encode_mouse_click_button2_returns_empty_bytes() -> None:
    assert _encode_mouse(["click", 4, 2, 2]) == b""


def test_encode_mouse_scroll_up_encodes_button64() -> None:
    result = _encode_mouse(["scroll", "up", 4, 2])
    assert b"\x1b[<64;" in result


def test_encode_mouse_scroll_down_encodes_button65() -> None:
    result = _encode_mouse(["scroll", "down", 4, 2])
    assert b"\x1b[<65;" in result


def test_encode_mouse_unknown_type_returns_empty_bytes() -> None:
    assert _encode_mouse(["unknown_event"]) == b""


# ---------------------------------------------------------------------------
# TerminalDisplay
# ---------------------------------------------------------------------------


def _render_display(display: TerminalDisplay) -> list[Any]:
    """Collect the Text lines yielded by TerminalDisplay.__rich_console__."""
    console = Console(file=StringIO(), highlight=False, markup=False)
    return list(display.__rich_console__(console, console.options))


def test_terminal_display_yields_all_lines() -> None:
    lines = [Text("line1"), Text("line2"), Text("line3")]
    display = TerminalDisplay(lines, cursor_x=0, cursor_y=0)
    yielded = _render_display(display)
    assert len(yielded) == 3


def test_terminal_display_cursor_row_has_reverse_span_at_cursor_column() -> None:
    line = Text("hello world")
    display = TerminalDisplay([line], cursor_x=3, cursor_y=0)
    yielded = _render_display(display)
    spans = [s for s in yielded[0]._spans if "reverse" in str(s.style)]
    assert any(s.start == 3 and s.end == 4 for s in spans)


def test_terminal_display_cursor_at_column_zero() -> None:
    line = Text("abc")
    display = TerminalDisplay([line], cursor_x=0, cursor_y=0)
    yielded = _render_display(display)
    spans = [s for s in yielded[0]._spans if "reverse" in str(s.style)]
    assert any(s.start == 0 and s.end == 1 for s in spans)


def test_terminal_display_non_cursor_row_has_no_reverse_spans() -> None:
    line0 = Text("cursor row")
    line1 = Text("other row")
    display = TerminalDisplay([line0, line1], cursor_x=0, cursor_y=0)
    yielded = _render_display(display)
    non_cursor_spans = [s for s in yielded[1]._spans if "reverse" in str(s.style)]
    assert len(non_cursor_spans) == 0


def test_terminal_display_cursor_on_second_row() -> None:
    line0 = Text("first")
    line1 = Text("second")
    display = TerminalDisplay([line0, line1], cursor_x=2, cursor_y=1)
    yielded = _render_display(display)
    spans_row1 = [s for s in yielded[1]._spans if "reverse" in str(s.style)]
    assert any(s.start == 2 and s.end == 3 for s in spans_row1)


# ---------------------------------------------------------------------------
# TerminalPyteScreen
# ---------------------------------------------------------------------------


def test_pyte_screen_set_margins_ignores_private_kwarg() -> None:
    screen = TerminalPyteScreen(80, 24)
    # Must not raise even when 'private' is present (pyte compatibility shim)
    screen.set_margins(top=1, bottom=24, private=True)


def test_pyte_screen_set_margins_works_without_private_kwarg() -> None:
    screen = TerminalPyteScreen(80, 24)
    screen.set_margins(top=1, bottom=24)


# ---------------------------------------------------------------------------
# Terminal.char_style_cmp
# ---------------------------------------------------------------------------


@pytest.fixture
def terminal_instance() -> Terminal:
    """A Terminal instance that is not started (no PTY)."""
    return Terminal("/bin/sh")


def test_char_style_cmp_identical_chars_returns_true(terminal_instance: Terminal) -> None:
    char = Char("a")
    assert terminal_instance.char_style_cmp(char, char) is True


def test_char_style_cmp_same_style_different_data_returns_true(terminal_instance: Terminal) -> None:
    char_a = Char("a")
    char_b = Char("b")
    assert terminal_instance.char_style_cmp(char_a, char_b) is True


def test_char_style_cmp_different_fg_returns_false(terminal_instance: Terminal) -> None:
    char_a = Char("a", fg="red")
    char_b = Char("a", fg="blue")
    assert terminal_instance.char_style_cmp(char_a, char_b) is False


def test_char_style_cmp_different_bg_returns_false(terminal_instance: Terminal) -> None:
    char_a = Char("a", bg="default")
    char_b = Char("a", bg="black")
    assert terminal_instance.char_style_cmp(char_a, char_b) is False


def test_char_style_cmp_different_bold_returns_false(terminal_instance: Terminal) -> None:
    char_a = Char("a", bold=True)
    char_b = Char("a", bold=False)
    assert terminal_instance.char_style_cmp(char_a, char_b) is False


def test_char_style_cmp_different_italic_returns_false(terminal_instance: Terminal) -> None:
    char_a = Char("a", italics=True)
    char_b = Char("a", italics=False)
    assert terminal_instance.char_style_cmp(char_a, char_b) is False


def test_char_style_cmp_different_reverse_returns_false(terminal_instance: Terminal) -> None:
    char_a = Char("a", reverse=True)
    char_b = Char("a", reverse=False)
    assert terminal_instance.char_style_cmp(char_a, char_b) is False


# ---------------------------------------------------------------------------
# Terminal.char_rich_style
# ---------------------------------------------------------------------------


def test_char_rich_style_returns_style_instance(terminal_instance: Terminal) -> None:
    char = Char("a")
    assert isinstance(terminal_instance.char_rich_style(char), Style)


def test_char_rich_style_bold_flag_propagated(terminal_instance: Terminal) -> None:
    char = Char("a", bold=True)
    style = terminal_instance.char_rich_style(char)
    assert style.bold is True


def test_char_rich_style_italic_flag_propagated(terminal_instance: Terminal) -> None:
    char = Char("a", italics=True)
    style = terminal_instance.char_rich_style(char)
    assert style.italic is True


def test_char_rich_style_non_bold_char_not_bold(terminal_instance: Terminal) -> None:
    char = Char("a", bold=False)
    style = terminal_instance.char_rich_style(char)
    assert not style.bold


def test_char_rich_style_invalid_color_falls_back_to_empty_style(terminal_instance: Terminal) -> None:
    # A color string that cannot be parsed by Rich should return Style()
    char = Char("a", fg="notacolor_xyz_invalid")
    style = terminal_instance.char_rich_style(char)
    assert isinstance(style, Style)


# ---------------------------------------------------------------------------
# Terminal.initial_display
# ---------------------------------------------------------------------------


def test_initial_display_has_single_empty_line(terminal_instance: Terminal) -> None:
    display = terminal_instance.initial_display()
    assert len(display.lines) == 1
    assert display.lines[0].plain == ""


def test_initial_display_cursor_at_origin(terminal_instance: Terminal) -> None:
    display = terminal_instance.initial_display()
    assert display.cursor_x == 0
    assert display.cursor_y == 0


# ---------------------------------------------------------------------------
# Terminal message classes
# ---------------------------------------------------------------------------


def test_closed_message_stores_terminal_widget() -> None:
    terminal = Terminal("/bin/sh")
    closed = Terminal.Closed(terminal)
    assert closed.terminal_widget is terminal


# ---------------------------------------------------------------------------
# Widget lifecycle: mount without starting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_mounts_with_started_false() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert terminal._started is False


@pytest.mark.asyncio
async def test_terminal_render_before_start_returns_initial_display() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = terminal.render()
        assert isinstance(result, TerminalDisplay)
        assert len(result.lines) == 1


# ---------------------------------------------------------------------------
# Widget lifecycle: start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_start_sets_started_true() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            assert terminal._started is True
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_terminal_stop_sets_started_false() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        terminal.stop()
        assert terminal._started is False


@pytest.mark.asyncio
async def test_terminal_start_is_idempotent() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            terminal.start()  # second call should be a no-op
            assert terminal._started is True
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_terminal_stop_without_start_is_safe() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.stop()  # must not raise
        assert terminal._started is False


@pytest.mark.asyncio
async def test_terminal_stop_resets_display_to_initial() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        terminal.stop()
        display = terminal.render()
        assert isinstance(display, TerminalDisplay)
        assert len(display.lines) == 1


@pytest.mark.asyncio
async def test_terminal_stop_cancels_and_clears_pending_rebuild_handle() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        loop = asyncio.get_running_loop()
        terminal._rebuild_handle = loop.call_later(100.0, lambda: None)
        assert terminal._rebuild_handle is not None

        terminal.stop()

        assert terminal._rebuild_handle is None


# ---------------------------------------------------------------------------
# Key handling: ignored when not started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_key_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        # send_queue is None; on_key must return early without using it
        await terminal.on_key(events.Key("a", character="a"))
        assert terminal.send_queue is None


# ---------------------------------------------------------------------------
# Key handling: routes characters and special keys to send_queue
#
# To avoid the race between on_key's put() and _run()'s get(), these tests
# set _started=True and assign a fresh send_queue manually.  The _run() task
# is never created, so items remain in the queue after on_key returns.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_key_puts_character_in_send_queue() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        await terminal.on_key(events.Key("b", character="b"))

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item == ["stdin", "b"]


@pytest.mark.asyncio
async def test_on_key_puts_escape_sequence_for_up_arrow() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        await terminal.on_key(events.Key("up", character=None))

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item == ["stdin", "\x1bOA"]


@pytest.mark.asyncio
async def test_on_key_ctrl_f1_releases_focus() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.focus()
        await pilot.pause()

        assert terminal.has_focus
        await terminal.on_key(events.Key("ctrl+f1", character=None))
        await pilot.pause()
        assert not terminal.has_focus


@pytest.mark.asyncio
async def test_on_key_unknown_key_without_character_puts_nothing() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        await terminal.on_key(events.Key("f99", character=None))

        assert terminal.send_queue.empty()


# ---------------------------------------------------------------------------
# Resize handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_resize_updates_ncol_and_nrow_to_widget_size() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        # on_resize ignores the event object; it reads self.size directly
        await terminal.on_resize(events.Resize(Size(100, 30), Size(0, 0)))

        assert terminal.ncol == terminal.size.width
        assert terminal.nrow == terminal.size.height


@pytest.mark.asyncio
async def test_on_resize_puts_set_size_message_in_send_queue() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        await terminal.on_resize(events.Resize(Size(80, 24), Size(0, 0)))

        item = terminal.send_queue.get_nowait()
        assert item[0] == "set_size"
        assert item[1] == terminal.nrow
        assert item[2] == terminal.ncol


# ---------------------------------------------------------------------------
# recv() behavior: helpers
#
# These tests exercise recv() in isolation — no PTY is forked.  We set up
# recv_queue manually and create only the recv() task.  This avoids the race
# between injected queue messages and real shell startup output.
# ---------------------------------------------------------------------------


async def _start_recv_only(terminal: Terminal) -> asyncio.Queue[list[object]]:
    """Start the recv() task without forking a PTY. Returns the recv_queue."""
    recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
    terminal.recv_queue = recv_queue
    terminal.recv_task_t = asyncio.create_task(terminal.recv())
    return recv_queue


async def _stop_recv_only(terminal: Terminal) -> None:
    """Cancel the recv() task started by _start_recv_only."""
    assert terminal.recv_task_t is not None
    terminal.recv_task_t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await terminal.recv_task_t


# ---------------------------------------------------------------------------
# _feed_stdout / _rebuild_display
# ---------------------------------------------------------------------------


def test_feed_stdout_updates_pyte_screen_without_changing_display(terminal_instance: Terminal) -> None:
    initial_display = terminal_instance._display
    terminal_instance._feed_stdout("Hello")
    # _display must not be replaced — only _rebuild_display does that
    assert terminal_instance._display is initial_display


@pytest.mark.asyncio
async def test_stdout_recv_defers_display_rebuild() -> None:
    """recv() schedules a deferred rebuild via call_later, not an immediate one."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["stdout", "Hello"])
            await asyncio.sleep(0.005)  # let recv() run, but the 16.6 ms timer has not fired yet
            assert terminal._rebuild_handle is not None  # timer is pending
            await asyncio.sleep(0.05)  # wait for the timer to fire
            assert terminal._rebuild_handle is None  # timer fired and cleared the handle
            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "Hello" in rendered
        finally:
            await _stop_recv_only(terminal)


def test_rebuild_display_reflects_pyte_screen_after_feed(terminal_instance: Terminal) -> None:
    terminal_instance._feed_stdout("Hello")
    terminal_instance._rebuild_display()
    rendered = "".join(line.plain for line in terminal_instance._display.lines)
    assert "Hello" in rendered


def test_feed_stdout_then_rebuild_is_equivalent_to_process_stdout() -> None:
    t1 = Terminal("/bin/sh")
    t2 = Terminal("/bin/sh")
    t1._process_stdout("Hello world")
    t2._feed_stdout("Hello world")
    t2._rebuild_display()
    rendered1 = "".join(line.plain for line in t1._display.lines)
    rendered2 = "".join(line.plain for line in t2._display.lines)
    assert rendered1 == rendered2


@pytest.mark.asyncio
async def test_feed_stdout_handles_type_error_from_pyte_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()

        def _raise_type_error(_chars: str) -> None:
            raise TypeError("bad feed")

        monkeypatch.setattr(terminal._stream, "feed", _raise_type_error)
        terminal._feed_stdout("some text")  # must not propagate TypeError


def test_rebuild_display_handles_adjacent_chars_with_different_styles() -> None:
    terminal = Terminal("/bin/sh")
    # Red 'A' then green 'B': adjacent chars with different fg — triggers style-change path
    terminal._feed_stdout("\x1b[31mA\x1b[32mB\x1b[0m")
    terminal._rebuild_display()
    rendered = "".join(line.plain for line in terminal._display.lines)
    assert "A" in rendered
    assert "B" in rendered


# ---------------------------------------------------------------------------
# recv() behavior: stdout updates the display
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdout_message_updates_display_content() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["stdout", "Hello"])
            await pilot.pause(delay=0.15)
            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "Hello" in rendered
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# recv() behavior: pre_cmd posts Terminal.PreCmd message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recv_setup_puts_set_size_in_send_queue() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["setup", {}])
            await asyncio.sleep(0.02)

            assert not terminal.send_queue.empty()
            msg = terminal.send_queue.get_nowait()
            assert msg[0] == "set_size"
            assert msg[1] == terminal.nrow
            assert msg[2] == terminal.ncol
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_recv_disconnect_posts_closed_and_stops_when_keep_alive_false() -> None:
    received_closed: list[Terminal.Closed] = []

    class ClosedCapturingApp(TerminalTestApp):
        def on_terminal_closed(self, event: Terminal.Closed) -> None:
            received_closed.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver(), keep_alive=False)
    app = ClosedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["disconnect"])
            await pilot.pause(delay=0.15)

            assert len(received_closed) == 1
            assert received_closed[0].terminal_widget is terminal
            assert terminal._started is False
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_recv_disconnect_calls_respawn_when_keep_alive_true() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver(), keep_alive=True)
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        respawn_called = False

        def fake_respawn() -> None:
            nonlocal respawn_called
            respawn_called = True

        terminal.respawn = fake_respawn  # type: ignore
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["disconnect"])
            await pilot.pause(delay=0.15)
            assert respawn_called is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_pre_cmd_message_posts_terminal_pre_cmd_event() -> None:
    received: list[Terminal.PreCmd] = []

    class CapturingApp(TerminalTestApp):
        def on_terminal_pre_cmd(self, event: Terminal.PreCmd) -> None:
            received.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = CapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["pre_cmd", "/home/user\n"])
            await pilot.pause(delay=0.15)
            assert len(received) == 1
            assert received[0].cwd == PurePath("/home/user")
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# recv() behavior: mouse tracking state via DECSET sequences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decset_1000h_enables_mouse_tracking() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            assert terminal.mouse_tracking is False
            await recv_q.put(["stdout", "\x1b[?1000h"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1000l_disables_mouse_tracking() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal.mouse_tracking = True
            await recv_q.put(["stdout", "\x1b[?1000l"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is False
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# _translate_terminal_color — fullmatch guard
# ---------------------------------------------------------------------------


def test_translate_terminal_color_7_digit_hex_not_prefixed() -> None:
    result = _translate_terminal_color("0000001")
    assert result == "0000001"


# ---------------------------------------------------------------------------
# Mouse click handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_click_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        # send_queue is None; on_click must return early without touching it
        await terminal.on_click(events.Click(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_on_click_ignored_when_mouse_tracking_disabled() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = False

        await terminal.on_click(events.Click(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))

        assert terminal.send_queue.empty()


@pytest.mark.asyncio
async def test_on_click_puts_click_message_in_send_queue_when_mouse_tracking_enabled() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_click(events.Click(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "click"
        assert item[1] == 5
        assert item[2] == 3


# ---------------------------------------------------------------------------
# Mouse scroll handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_scroll_down_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await terminal.on_mouse_scroll_down(events.MouseScrollDown(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_on_scroll_down_ignored_when_mouse_tracking_disabled() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = False

        await terminal.on_mouse_scroll_down(events.MouseScrollDown(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))

        assert terminal.send_queue.empty()


@pytest.mark.asyncio
async def test_on_scroll_down_puts_scroll_message_in_send_queue() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_mouse_scroll_down(events.MouseScrollDown(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "scroll"
        assert item[1] == "down"


@pytest.mark.asyncio
async def test_on_scroll_up_puts_scroll_message_in_send_queue() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_mouse_scroll_up(events.MouseScrollUp(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "scroll"
        assert item[1] == "up"


@pytest.mark.asyncio
async def test_on_scroll_up_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await terminal.on_mouse_scroll_up(events.MouseScrollUp(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_on_scroll_up_ignored_when_mouse_tracking_disabled() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = False

        await terminal.on_mouse_scroll_up(events.MouseScrollUp(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False))

        assert terminal.send_queue.empty()


# ---------------------------------------------------------------------------
# Terminal.send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await terminal.send("hello")  # must not raise
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_send_writes_data_to_backend() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True

        await terminal.send("hello")

        assert b"hello" in backend.writes


# ---------------------------------------------------------------------------
# recv() behavior: extended mouse tracking modes (1002 / 1003 / 1006)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decset_1002h_enables_mouse_tracking() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            assert terminal.mouse_tracking is False
            await recv_q.put(["stdout", "\x1b[?1002h"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1003h_enables_mouse_tracking() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            assert terminal.mouse_tracking is False
            await recv_q.put(["stdout", "\x1b[?1003h"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1006h_enables_mouse_tracking() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            assert terminal.mouse_tracking is False
            await recv_q.put(["stdout", "\x1b[?1006h"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1002l_disables_mouse_tracking() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal.mouse_tracking = True
            await recv_q.put(["stdout", "\x1b[?1002l"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is False
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1003l_disables_mouse_tracking() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal.mouse_tracking = True
            await recv_q.put(["stdout", "\x1b[?1003l"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is False
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# Rendering: cursor reverse stored in raw display lines (double-stylization bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_reverse_span_not_stored_in_raw_display_lines() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["stdout", "hello"])
            await pilot.pause(delay=0.15)
            cursor_y = terminal._display.cursor_y
            line = terminal._display.lines[cursor_y]
            reverse_spans = [s for s in line._spans if s.style == "reverse"]
            # In the correct implementation cursor reverse is applied only inside
            # TerminalDisplay.__rich_console__, so the stored line carries no reverse spans.
            assert len(reverse_spans) == 0
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# send_silent
# ---------------------------------------------------------------------------


def test_send_is_callable() -> None:
    terminal = Terminal("/usr/bin/zsh", id="t_silent_callable", keep_alive=False)
    assert callable(terminal.send)


@pytest.mark.asyncio
async def test_draining_suppresses_display_rebuild_until_pre_cmd() -> None:
    """While _draining is True, stdout is discarded (not fed to pyte) and the display
    does not change.  When pre_cmd fires, _draining is cleared, backend is resumed,
    and the content that arrived during draining is absent from the screen."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        initial_display = terminal._display
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._draining = True

            # stdout while draining: discarded, display must NOT change
            await recv_q.put(["stdout", "SILENT_CONTENT"])
            await pilot.pause(delay=0.1)
            assert terminal._display is initial_display  # no rebuild was scheduled

            # pre_cmd fires: _draining cleared, backend resumed, no screen reset
            await recv_q.put(["pre_cmd", "/some/path\n"])
            await pilot.pause(delay=0.1)
            assert terminal._draining is False
            assert backend.resume_count == 1
            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "SILENT_CONTENT" not in rendered
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_draining_flag_set_by_send_silent() -> None:
    """send with mode='silent' sets _draining to True and writes data to backend."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        terminal._started = True
        try:
            assert terminal._draining is False
            await terminal.send("some command\n", mode="silent")
            assert terminal._draining is True
            assert b"some command\n" in backend.writes
        finally:
            terminal._started = False
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_normal_send_after_pre_cmd_resets_drain_appears_on_screen() -> None:
    """After pre_cmd clears _draining, subsequent stdout is fed to pyte and rendered.
    Stdout that arrived while draining is discarded and never rendered."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._draining = True
            await recv_q.put(["stdout", "SILENT_CONTENT"])
            await recv_q.put(["pre_cmd", "/some/path\n"])
            await recv_q.put(["stdout", "VISIBLE_CONTENT"])
            await pilot.pause(delay=0.2)
            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "SILENT_CONTENT" not in rendered
            assert "VISIBLE_CONTENT" in rendered
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# has_input
# ---------------------------------------------------------------------------


def test_has_input_returns_false_when_cursor_at_prompt_position(terminal_instance: Terminal) -> None:
    terminal_instance._prompt_cursor_x = 5
    terminal_instance._screen.cursor.x = 5
    assert terminal_instance.has_input() is False


def test_has_input_returns_true_when_cursor_past_prompt_position() -> None:
    # Requires a driver with supports_prompt_ready=True (ZshDriver) to track prompts.
    terminal = Terminal("/bin/zsh", driver=ZshDriver())
    terminal._prompt_cursor_x = 5
    terminal._screen.cursor.x = 8
    assert terminal.has_input() is True


def test_has_input_returns_false_for_fallback_driver_regardless_of_cursor() -> None:
    # FallbackDriver cannot track prompt position, so has_input() always returns False.
    terminal = Terminal("/bin/sh", driver=FallbackDriver())
    terminal._prompt_cursor_x = 0
    terminal._screen.cursor.x = 8  # cursor past start, but driver can't track
    assert terminal.has_input() is False


def test_has_input_returns_false_on_fresh_terminal(terminal_instance: Terminal) -> None:
    # Both _prompt_cursor_x and screen cursor start at 0
    assert terminal_instance.has_input() is False


def test_prompt_ready_message_sets_cursor_snapshot() -> None:
    """_handle_prompt_ready() must snapshot the cursor position into _prompt_cursor_x/_prompt_cursor_y."""
    terminal = Terminal("/bin/zsh", driver=ZshDriver())
    terminal._stream.feed("$ ")  # moves cursor to x=2
    terminal._handle_prompt_ready()
    assert terminal._prompt_cursor_x == terminal._screen.cursor.x
    assert terminal._prompt_cursor_y == terminal._screen.cursor.y


def test_has_input_returns_true_for_ssh_zsh_driver() -> None:
    """has_input() must work for SSH terminals using ZshDriver (supports_prompt_ready=True)."""
    # SSH-style: no stop/resume, but supports prompt_ready
    terminal = Terminal("/bin/zsh", driver=ZshDriver(stop_resume=False))
    terminal._stream.feed("$ ")
    terminal._handle_prompt_ready()
    terminal._stream.feed("ls")
    assert terminal.has_input() is True


def test_has_input_false_after_pre_cmd_and_prompt_ready() -> None:
    """has_input() must return False right after ["prompt_ready"] with no user input."""
    terminal = Terminal("/bin/zsh", driver=ZshDriver())
    terminal._stream.feed("$ ")
    terminal._handle_prompt_ready()
    # cursor is exactly at prompt position — no user input yet
    assert terminal.has_input() is False


@pytest.mark.asyncio
async def test_has_input_true_after_prompt_and_user_input() -> None:
    """After prompt + user input, cursor moves right and has_input() returns True."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["pre_cmd", "/home/user\n"])
            await recv_q.put(["stdout", "$ "])  # prompt
            await pilot.pause(delay=0.15)
            await recv_q.put(["prompt_ready"])  # snapshot prompt cursor position
            await pilot.pause(delay=0.05)
            await recv_q.put(["stdout", "ls"])  # user typed "ls"
            await pilot.pause(delay=0.15)
            assert terminal.has_input() is True
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# set_terminal_directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_terminal_directory_returns_given_path_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        target = PurePath("/some/path")
        result = await terminal.set_terminal_directory(target)
        assert result == target


@pytest.mark.asyncio
async def test_set_terminal_directory_returns_cwd_with_fallback_driver() -> None:
    """FallbackDriver has no nav_future, so _cwd or path is returned immediately."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=FallbackDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._cwd = PurePath("/existing/cwd")
        result = await terminal.set_terminal_directory(PurePath("/target"))
        assert result == PurePath("/existing/cwd")


@pytest.mark.asyncio
async def test_set_terminal_directory_returns_path_when_cwd_none_and_fallback() -> None:
    """When _cwd is None and FallbackDriver, _cwd or path evaluates to path."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=FallbackDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._cwd = None
        target = PurePath("/target")
        result = await terminal.set_terminal_directory(target)
        assert result == target


@pytest.mark.asyncio
async def test_set_terminal_directory_writes_cd_to_backend_and_sets_draining() -> None:
    """set_terminal_directory writes cd command to backend and sets _draining."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True

        nav_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/tmp/test")))  # noqa: S108
        await asyncio.sleep(0)  # let the task start and write the cd

        assert terminal._draining is True
        # Only cd command written (no KILL_LINE since has_input() is False)
        cd_writes = [w for w in backend.writes if b"/tmp/test" in w]
        assert len(cd_writes) == 1
        assert cd_writes[0].endswith(b"\n")

        nav_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await nav_task


@pytest.mark.asyncio
async def test_set_terminal_directory_no_pending_yank_when_no_input() -> None:
    """If the cursor is at the prompt position, no yank should be scheduled."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._prompt_cursor_x = 0
        terminal._screen.cursor.x = 0  # no user input

        nav_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/tmp")))  # noqa: S108
        await asyncio.sleep(0)

        assert terminal._pending_yank is False
        # No KILL_LINE written
        kill_line_writes = [w for w in backend.writes if b"\x15" in w]
        assert len(kill_line_writes) == 0

        nav_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await nav_task


@pytest.mark.asyncio
async def test_set_terminal_directory_sets_pending_yank_when_input_present() -> None:
    """If the user has typed text, _pending_yank must be set so it is restored later."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._prompt_cursor_x = 2
        terminal._screen.cursor.x = 6  # user has typed 4 chars

        nav_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/tmp")))  # noqa: S108
        await asyncio.sleep(0)

        assert terminal._pending_yank is True
        # KILL_LINE should have been written to backend
        kill_line_writes = [w for w in backend.writes if b"\x15" in w]
        assert len(kill_line_writes) == 1

        nav_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await nav_task


# ---------------------------------------------------------------------------
# Yank mechanism: pre_cmd triggers yank when _pending_yank is True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yank_and_end_of_line_sent_on_pre_cmd_when_pending() -> None:
    """When _pending_yank is True and _draining is True, the pre_cmd handler
    must write YANK + END_OF_LINE to the backend, resume, and clear state."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._draining = True
            terminal._pending_yank = True

            await recv_q.put(["pre_cmd", "/some/path\n"])
            await pilot.pause(delay=0.15)

            assert terminal._pending_yank is False
            assert terminal._draining is False
            # Backend should have received YANK + END_OF_LINE
            yank_writes = [w for w in backend.writes if b"\x19" in w]
            assert len(yank_writes) == 1
            assert b"\x05" in yank_writes[0]  # END_OF_LINE
            assert backend.resume_count == 1
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_yank_not_sent_when_pending_yank_is_false() -> None:
    """When _pending_yank is False, no yank must be written after pre_cmd."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._draining = True
            terminal._pending_yank = False

            await recv_q.put(["pre_cmd", "/some/path\n"])
            await pilot.pause(delay=0.15)

            # No yank should have been written
            yank_writes = [w for w in backend.writes if b"\x19" in w]
            assert len(yank_writes) == 0
            # But resume should still happen
            assert backend.resume_count == 1
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_prompt_cursor_snapshotted_on_prompt_ready_not_at_pre_cmd() -> None:
    """_prompt_cursor_x/_prompt_cursor_y must be set when ["prompt_ready"] arrives,
    not when pre_cmd fires and not during _rebuild_display."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["pre_cmd", "/home/user\n"])
            # pre_cmd fired but no prompt_ready yet — snapshot must not have moved.
            await asyncio.sleep(0.005)
            assert terminal._prompt_cursor_x == 0

            # Send prompt text and wait for rebuild — still no prompt_ready.
            await recv_q.put(["stdout", "$ "])
            await pilot.pause(delay=0.15)
            assert terminal._prompt_cursor_x == 0  # rebuild does not snapshot

            # prompt_ready arrives: snapshot taken at current cursor position.
            await recv_q.put(["prompt_ready"])
            await asyncio.sleep(0.005)
            assert terminal._prompt_cursor_x == terminal._screen.cursor.x
            assert terminal._prompt_cursor_y == terminal._screen.cursor.y
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# Race condition tests (adapted for SIGSTOP synchronisation model)
#
# With SIGSTOP, the shell freezes after every precmd.  It cannot produce a
# second pre_cmd until explicitly resumed via backend.resume().  This
# eliminates the class of races where stale pre_cmds could clear draining
# prematurely while the shell was still generating output.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_race_a_pre_cmd_during_draining_resumes_and_clears() -> None:
    """With SIGSTOP, when pre_cmd arrives while _draining is True, the recv()
    handler resumes the shell and clears draining.

    This is correct behaviour under SIGSTOP: the pre_cmd corresponds to the
    shell being frozen after its precmd hook.  The resume lets it process the
    queued cd command.  Since cd produces no stdout, no echo leaks onto the
    screen.
    """
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True

        # Stale pre_cmd from a previous navigation is already in the queue.
        recv_q: asyncio.Queue[list[object]] = asyncio.Queue()
        terminal.recv_queue = recv_q
        await recv_q.put(["pre_cmd", "/old/path\n"])

        # Create the recv() task.
        terminal.recv_task_t = asyncio.create_task(terminal.recv())

        # Navigation sets _draining=True and writes cd to backend.
        terminal._draining = True
        terminal._backend.write(b"cd /new/path\n")

        # Yield to the event loop: recv() runs, processes the pre_cmd.
        await pilot.pause(delay=0.05)

        # With SIGSTOP the pre_cmd triggers resume and clears draining.
        assert terminal._draining is False
        assert backend.resume_count == 1

        terminal.recv_task_t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await terminal.recv_task_t


@pytest.mark.asyncio
async def test_race_c_two_navigations_first_pre_cmd_resumes_shell() -> None:
    """With SIGSTOP, two rapid set_terminal_directory calls buffer both cd commands.

    When the first pre_cmd arrives, _nav_pending decrements from 2 to 1.
    Draining stays on because a second navigation is still in flight.
    The shell is resumed so it can process the second cd.
    When the second pre_cmd arrives, _nav_pending reaches 0 and draining clears.
    """
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        terminal._started = True
        try:
            terminal._prompt_cursor_x = 0

            # Two rapid navigations — both cd commands are written to the backend.
            nav_task_a = asyncio.create_task(
                terminal.set_terminal_directory(PurePath("/tmp/A")),  # noqa: S108
            )
            await asyncio.sleep(0)
            nav_task_b = asyncio.create_task(
                terminal.set_terminal_directory(PurePath("/tmp/B")),  # noqa: S108
            )
            await asyncio.sleep(0)
            assert terminal._draining is True
            assert terminal._nav_pending == 2

            # First pre_cmd: nav_pending 2→1, draining stays True, shell resumed.
            await recv_q.put(["pre_cmd", "/home/user/A\n"])
            await pilot.pause(delay=0.05)
            assert terminal._draining is True  # still draining — second nav pending
            assert terminal._nav_pending == 1
            assert backend.resume_count == 1

            # Second pre_cmd: nav_pending 1→0, draining clears.
            await recv_q.put(["pre_cmd", "/home/user/B\n"])
            await pilot.pause(delay=0.05)
            assert terminal._draining is False
            assert terminal._nav_pending == 0
            assert backend.resume_count == 2

            # Prompt stdout then prompt_ready snapshots cursor.
            await recv_q.put(["stdout", "\r\n/home/user/projects $ "])
            await pilot.pause(delay=0.15)
            await recv_q.put(["prompt_ready"])
            await asyncio.sleep(0.005)
            assert terminal._prompt_cursor_x == terminal._screen.cursor.x

            # Cursor is at the end of the prompt; user has not typed anything.
            assert terminal.has_input() is False

            # The nav future should be resolved now.
            assert nav_task_b.done()
            assert nav_task_a.done()
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# request_cd / PathChanged: user_initiated flag & race condition tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_cd_does_nothing_when_not_started() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert terminal._started is False
        terminal.request_cd(PurePath("/some/path"))
        assert len(backend.writes) == 0


@pytest.mark.asyncio
async def test_request_cd_skips_when_already_at_current_path() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._nav_pending = 0
        terminal._cwd = PurePath("/current/path")
        terminal.request_cd(PurePath("/current/path"))
        assert len(backend.writes) == 0


@pytest.mark.asyncio
async def test_request_cd_fallback_driver_writes_cd_without_draining() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=FallbackDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal.request_cd(PurePath("/target/path"))
        assert len(backend.writes) >= 1
        assert b"".join(backend.writes).endswith(b"\n")
        assert terminal._draining is False


class PathChangedCapturingApp(TerminalTestApp):
    def __init__(self, terminal: Terminal) -> None:
        super().__init__(terminal)
        self.path_changed_events: list[Terminal.PathChanged] = []

    def on_terminal_path_changed(self, event: Terminal.PathChanged) -> None:
        self.path_changed_events.append(event)


@pytest.mark.asyncio
async def test_request_cd_path_changed_is_not_user_initiated() -> None:
    """PathChanged from a programmatic request_cd has user_initiated=False."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal.request_cd(PurePath("/tmp/a"))  # noqa: S108
            assert terminal._nav_pending == 1

            # Simulate precmd acknowledging the cd
            await recv_q.put(["pre_cmd", "/home/user/a"])
            await pilot.pause(delay=0.15)

            assert len(app.path_changed_events) == 1
            assert app.path_changed_events[0].user_initiated is False
            assert app.path_changed_events[0].cwd == PurePath("/home/user/a")
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_user_cd_path_changed_is_user_initiated() -> None:
    """PathChanged from a user-typed cd (no request_cd) has user_initiated=True."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            # No request_cd — user typed cd in the terminal
            assert terminal._nav_pending == 0

            await recv_q.put(["pre_cmd", "/home/user/b"])
            await pilot.pause(delay=0.15)

            assert len(app.path_changed_events) == 1
            assert app.path_changed_events[0].user_initiated is True
            assert app.path_changed_events[0].cwd == PurePath("/home/user/b")
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_rapid_request_cd_only_last_fires_path_changed() -> None:
    """Multiple rapid request_cd calls only produce PathChanged for the final cd."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal.request_cd(PurePath("/a"))
            terminal.request_cd(PurePath("/b"))
            terminal.request_cd(PurePath("/c"))
            assert terminal._nav_pending == 3

            # First two precmds: intermediate, no PathChanged
            await recv_q.put(["pre_cmd", "/a"])
            await pilot.pause(delay=0.15)
            assert len(app.path_changed_events) == 0

            await recv_q.put(["pre_cmd", "/b"])
            await pilot.pause(delay=0.15)
            assert len(app.path_changed_events) == 0

            # Last precmd: PathChanged fires
            await recv_q.put(["pre_cmd", "/c"])
            await pilot.pause(delay=0.15)
            assert len(app.path_changed_events) == 1
            assert app.path_changed_events[0].cwd == PurePath("/c")
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_request_cd_with_panel_id_prepends_nn_panel_to_cd_command() -> None:
    """request_cd with panel_id prepends '_NN_PANEL=left; ' before the cd command."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True

        terminal.request_cd(PurePath("/new/dir"), "left")
        await asyncio.sleep(0)

        cd_writes = [w for w in backend.writes if b"/new/dir" in w]
        assert len(cd_writes) == 1
        assert b"_NN_PANEL=left" in cd_writes[0]
        assert b"_NN_PANEL=left" in cd_writes[0].split(b"/new/dir")[0]  # prefix comes first


@pytest.mark.asyncio
async def test_request_cd_same_path_with_panel_id_sends_true_not_cd() -> None:
    """When path == _cwd and panel_id is given, 'true' is sent to update _NN_PANEL
    without triggering a real cd (avoids chpwd hooks)."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._cwd = PurePath("/current/dir")

        terminal.request_cd(PurePath("/current/dir"), "right")
        await asyncio.sleep(0)

        # Must write _NN_PANEL=right; true (not cd)
        panel_writes = [w for w in backend.writes if b"_NN_PANEL=right" in w]
        assert len(panel_writes) == 1
        assert b"true" in panel_writes[0]
        assert b"cd" not in panel_writes[0] or b"_NN_PANEL" in panel_writes[0].split(b"cd")[0]

        # _nav_pending must be incremented (not short-circuited)
        assert terminal._nav_pending == 1


@pytest.mark.asyncio
async def test_request_cd_same_path_with_panel_id_does_not_post_path_changed() -> None:
    """Same-path request_cd with panel_id updates _NN_PANEL only; PathChanged must not fire."""
    received: list[Terminal.PathChanged] = []

    class CapturingApp(TerminalTestApp):
        def on_terminal_path_changed(self, event: Terminal.PathChanged) -> None:
            received.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = CapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._cwd = PurePath("/current/dir")

            terminal.request_cd(PurePath("/current/dir"), "right")
            await asyncio.sleep(0)

            # pre_cmd arrives: _nav_pending 1→0, draining clears, but cwd_changed=False
            await recv_q.put(["pre_cmd", "/current/dir\n", "right"])
            await pilot.pause(delay=0.15)

            # PathChanged must NOT be posted (path did not change)
            assert len(received) == 0
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_request_cd_same_path_without_panel_id_short_circuits() -> None:
    """When path == _cwd and no panel_id is given, the old short-circuit still fires:
    nothing is written to the backend and _nav_pending stays at 0."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._cwd = PurePath("/current/dir")
        terminal._nav_pending = 0
        initial_write_count = len(backend.writes)

        terminal.request_cd(PurePath("/current/dir"))  # no panel_id
        await asyncio.sleep(0)

        assert terminal._nav_pending == 0
        assert len(backend.writes) == initial_write_count  # nothing written


@pytest.mark.asyncio
async def test_path_changed_event_carries_panel_id() -> None:
    """When pre_cmd arrives with panel_id, the PathChanged message must carry it."""
    received: list[Terminal.PathChanged] = []

    class CapturingApp(TerminalTestApp):
        def on_terminal_path_changed(self, event: Terminal.PathChanged) -> None:
            received.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = CapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            # Simulate pre_cmd with panel_id from the updated pty_backend
            await recv_q.put(["pre_cmd", "/home/user\n", "left"])
            await pilot.pause(delay=0.15)

            assert len(received) == 1
            assert received[0].panel_id == "left"
            assert received[0].user_initiated is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_path_changed_event_panel_id_empty_when_not_in_pre_cmd() -> None:
    """Legacy pre_cmd with no panel_id element posts PathChanged with panel_id=''."""
    received: list[Terminal.PathChanged] = []

    class CapturingApp(TerminalTestApp):
        def on_terminal_path_changed(self, event: Terminal.PathChanged) -> None:
            received.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = CapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            # 2-element pre_cmd (old format / FallbackDriver)
            await recv_q.put(["pre_cmd", "/home/user\n"])
            await pilot.pause(delay=0.15)

            assert len(received) == 1
            assert received[0].panel_id == ""
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# respawn()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respawn_tears_down_backend_and_starts_fresh() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        await asyncio.sleep(0.01)

        backend.torn_down = False
        backend.opened = False

        terminal.respawn()
        await asyncio.sleep(0.01)

        assert backend.torn_down is True
        assert backend.opened is True
        assert terminal._run_task is not None

        terminal.stop()


# ---------------------------------------------------------------------------
# _run() send loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_processes_stdin_and_writes_to_backend() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        await asyncio.sleep(0.02)

        assert terminal.send_queue is not None
        terminal.send_queue.put_nowait(["stdin", "hello"])
        await asyncio.sleep(0.02)

        assert any(b"hello" in w for w in backend.writes)

        terminal.stop()


@pytest.mark.asyncio
async def test_run_processes_click_message_with_button1() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        await asyncio.sleep(0.02)

        assert terminal.send_queue is not None
        terminal.send_queue.put_nowait(["click", 5, 3, 1])
        await asyncio.sleep(0.02)

        assert any(b"\x1b[<0;" in w for w in backend.writes)

        terminal.stop()


@pytest.mark.asyncio
async def test_run_processes_scroll_message() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        await asyncio.sleep(0.02)

        assert terminal.send_queue is not None
        terminal.send_queue.put_nowait(["scroll", "up", 5, 3])
        await asyncio.sleep(0.02)

        assert any(b"\x1b[<64;" in w for w in backend.writes)

        terminal.stop()


@pytest.mark.asyncio
async def test_run_processes_set_size_message() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        await asyncio.sleep(0.02)

        initial_count = len(backend.resize_calls)
        assert terminal.send_queue is not None
        terminal.send_queue.put_nowait(["set_size", 30, 100])
        await asyncio.sleep(0.02)

        assert len(backend.resize_calls) > initial_count
        assert backend.resize_calls[-1] == (30, 100)

        terminal.stop()


# ---------------------------------------------------------------------------
# Task 5: init_code() no-arg and _handle_pre_cmd uses path directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_backend_calls_init_code_with_no_args() -> None:
    """init_code() must be called without arguments after the pipe removal."""
    from unittest.mock import MagicMock

    backend = FakePtyBackend()
    driver = MagicMock()
    driver.supports_stop_resume = False
    driver.init_code.return_value = ""
    driver.supports_precmd = True

    terminal = Terminal("/bin/sh", backend=backend, driver=driver)
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            driver.init_code.assert_called_once_with()
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_handle_pre_cmd_updates_cwd_from_plain_path() -> None:
    """_handle_pre_cmd uses the raw string as a path directly (no parse_precmd_payload)."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver(stop_resume=False))
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            recv_q.put_nowait(["pre_cmd", "/home/user/work"])
            await pilot.pause(delay=0.05)
            assert terminal._cwd == PurePath("/home/user/work")
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_third_party_chpwd_osc7_ignored_when_stop_resume_active() -> None:
    """Non-NN OSC 7 (from_nn=False) is ignored by _handle_pre_cmd when NN hooks are active.

    Third-party zsh chpwd hooks (oh-my-zsh, powerlevel10k, etc.) emit OSC 7 without
    the panel= prefix.  When ZshDriver.supports_stop_resume is True, these events must
    not decrement _nav_pending, update _cwd, or post PathChanged — otherwise rapid
    panel toggling causes the display to cycle through directories.
    """
    backend = FakePtyBackend()
    # ZshDriver() uses stop_resume=True by default — NN hooks are active.
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal.request_cd(PurePath("/target"))
            assert terminal._nav_pending == 1
            initial_cwd = terminal._cwd

            # Third-party chpwd fires first (from_nn=False) — must be ignored.
            await recv_q.put(["pre_cmd", "/target", "", False])
            await pilot.pause(delay=0.1)

            assert terminal._nav_pending == 1, "non-NN event must not decrement _nav_pending"
            assert terminal._cwd == initial_cwd, "non-NN event must not update _cwd"
            assert len(app.path_changed_events) == 0, "non-NN event must not post PathChanged"

            # NN precmd fires next (from_nn=True) — must be processed normally.
            await recv_q.put(["pre_cmd", "/target", "left", True])
            await pilot.pause(delay=0.1)

            assert terminal._nav_pending == 0
            assert terminal._cwd == PurePath("/target")
            assert len(app.path_changed_events) == 1
            assert app.path_changed_events[0].user_initiated is False
        finally:
            await _stop_recv_only(terminal)
