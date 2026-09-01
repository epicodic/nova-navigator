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
from nova_navigator.terminal.shell_editor_protocol import (
    PROBE_SEQUENCE,
    RESTORE_SEQUENCE,
    STASH_SEQUENCE,
    EditorOperation,
    EditorResponse,
)
from nova_navigator.terminal.terminal import (
    Terminal,
    TerminalDisplay,
    TerminalPyteScreen,
    _encode_mouse,
    _NavigationRequest,
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


# ---------------------------------------------------------------------------
# submit_enter / editor_response correlation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editor_ready_alone_does_not_enable_editor_protocol() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(
                [
                    "editor_response",
                    EditorResponse(
                        nonce=terminal._editor_nonce,
                        operation=EditorOperation.READY,
                        sequence=0,
                        buffer_length=0,
                        cursor=0,
                    ),
                ]
            )
            await pilot.pause(delay=0.1)

            assert terminal._editor_ready_received is True
            assert terminal._editor_available is False
            assert PROBE_SEQUENCE not in backend.writes
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_editor_protocol_enabled_only_after_ready_prompt_and_startup_probe() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(
                [
                    "editor_response",
                    EditorResponse(
                        nonce=terminal._editor_nonce,
                        operation=EditorOperation.READY,
                        sequence=0,
                        buffer_length=0,
                        cursor=0,
                    ),
                ]
            )
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.1)

            assert terminal._editor_available is False
            assert backend.writes.count(PROBE_SEQUENCE) == 1

            await recv_q.put(
                [
                    "editor_response",
                    EditorResponse(
                        nonce=terminal._editor_nonce,
                        operation=EditorOperation.PROBE,
                        sequence=1,
                        buffer_length=5,
                        cursor=0,
                    ),
                ]
            )
            await pilot.pause(delay=0.1)

            assert terminal._editor_available is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_submit_enter_delegates_when_probe_reports_empty_buffer() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            submit_task = asyncio.create_task(terminal.submit_enter(owner="left"))
            await pilot.pause(delay=0.05)
            assert backend.writes[-1] == PROBE_SEQUENCE

            await recv_q.put(
                [
                    "editor_response",
                    EditorResponse(
                        nonce=terminal._editor_nonce,
                        operation=EditorOperation.PROBE,
                        sequence=1,
                        buffer_length=0,
                        cursor=0,
                    ),
                ]
            )

            assert await submit_task is False
            assert backend.writes.count(b"\r") == 0
            assert terminal._command_owner is None
            assert terminal._at_prompt is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("length", "cursor"),
    [
        (1, 0),  # whitespace/pasted text at cursor zero must still forward
        (5, 5),
        (3, 1),  # multiline probe result is still non-empty by length
    ],
)
async def test_submit_enter_forwards_when_probe_reports_nonempty(
    length: int,
    cursor: int,
) -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            submit_task = asyncio.create_task(terminal.submit_enter(owner="left"))
            await pilot.pause(delay=0.05)
            assert backend.writes[-1] == PROBE_SEQUENCE

            await recv_q.put(
                [
                    "editor_response",
                    EditorResponse(
                        nonce=terminal._editor_nonce,
                        operation=EditorOperation.PROBE,
                        sequence=1,
                        buffer_length=length,
                        cursor=cursor,
                    ),
                ]
            )

            assert await submit_task is True
            assert backend.writes[-1] == b"\r"
            assert terminal._command_owner == "left"
            assert terminal._at_prompt is False
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_submit_enter_forwards_when_not_at_prompt() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._editor_available = True
        terminal._at_prompt = False

        assert await terminal.submit_enter(owner="left") is True
        assert backend.writes[-1] == b"\r"
        assert PROBE_SEQUENCE not in backend.writes


@pytest.mark.asyncio
async def test_submit_enter_forwards_when_editor_unavailable() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._editor_available = False
        terminal._at_prompt = True

        assert await terminal.submit_enter(owner="left") is True
        assert backend.writes[-1] == b"\r"
        assert PROBE_SEQUENCE not in backend.writes


@pytest.mark.asyncio
async def test_submit_enter_forwards_when_probe_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        terminal._started = True
        terminal._editor_available = True
        terminal._at_prompt = True
        monkeypatch.setattr("nova_navigator.terminal.terminal._EDITOR_RESPONSE_TIMEOUT", 0.01)

        assert await terminal.submit_enter(owner="left") is True
        assert backend.writes[-1] == b"\r"
        assert terminal._editor_available is False


@pytest.mark.asyncio
async def test_submit_enter_forwards_on_wrong_operation_correlation() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True
            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr("nova_navigator.terminal.terminal._EDITOR_RESPONSE_TIMEOUT", 0.01)
            try:
                submit_task = asyncio.create_task(terminal.submit_enter(owner="left"))
                await pilot.pause(delay=0.05)
                await recv_q.put(
                    [
                        "editor_response",
                        EditorResponse(
                            nonce=terminal._editor_nonce,
                            operation=EditorOperation.STASH,
                            sequence=1,
                            buffer_length=0,
                            cursor=0,
                        ),
                    ]
                )
                assert await submit_task is True
            finally:
                monkeypatch.undo()

            assert backend.writes[-1] == b"\r"
            assert terminal._editor_available is False
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_submit_enter_serializes_repeated_calls() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            first = asyncio.create_task(terminal.submit_enter(owner="left"))
            second = asyncio.create_task(terminal.submit_enter(owner="right"))
            await pilot.pause(delay=0.05)
            assert backend.writes.count(PROBE_SEQUENCE) == 1

            await recv_q.put(
                [
                    "editor_response",
                    EditorResponse(
                        nonce=terminal._editor_nonce,
                        operation=EditorOperation.PROBE,
                        sequence=1,
                        buffer_length=0,
                        cursor=0,
                    ),
                ]
            )
            await pilot.pause(delay=0.05)
            assert await first is False

            # Second call starts only after the first resolves.
            assert backend.writes.count(PROBE_SEQUENCE) == 2
            await recv_q.put(
                [
                    "editor_response",
                    EditorResponse(
                        nonce=terminal._editor_nonce,
                        operation=EditorOperation.PROBE,
                        sequence=2,
                        buffer_length=1,
                        cursor=1,
                    ),
                ]
            )
            assert await second is True
            assert terminal._command_owner == "right"
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_pending_editor_request_is_cancelled_on_disconnect() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver(), keep_alive=False)
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            submit_task = asyncio.create_task(terminal.submit_enter(owner="left"))
            await pilot.pause(delay=0.05)
            await recv_q.put(["disconnect"])
            await pilot.pause(delay=0.1)

            assert await submit_task is True
            assert terminal._editor_response_future is None
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


def _stash_response(terminal: Terminal, sequence: int = 1) -> list[object]:
    """Build a recv() message acknowledging a stash request for *terminal*."""
    return [
        "editor_response",
        EditorResponse(
            nonce=terminal._editor_nonce,
            operation=EditorOperation.STASH,
            sequence=sequence,
            buffer_length=0,
            cursor=0,
        ),
    ]


def _restore_response(terminal: Terminal, sequence: int = 2) -> list[object]:
    """Build a recv() message acknowledging a restore request for *terminal*."""
    return [
        "editor_response",
        EditorResponse(
            nonce=terminal._editor_nonce,
            operation=EditorOperation.RESTORE,
            sequence=sequence,
            buffer_length=0,
            cursor=0,
        ),
    ]


# ---------------------------------------------------------------------------
# Navigation coordinator: stash / cd / restore transaction ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_navigation_writes_stash_sequence_first() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)

            assert backend.writes[-1] == STASH_SEQUENCE
            assert terminal._draining is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_does_not_send_cd_before_stash_acknowledgement() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)

            assert not any(b"/target" in w for w in backend.writes)
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_sends_history_excluded_cd_after_stash_ack() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)
            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)

            cd_writes = [w for w in backend.writes if b"/target" in w]
            assert len(cd_writes) == 1
            assert cd_writes[0].startswith(b" ")  # leading space excludes it from shell history
            assert cd_writes[0].endswith(b"\n")
            assert b"_NN_PANEL" not in cd_writes[0]
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_full_transaction_write_order() -> None:
    """Writes occur in order: stash, cd, restore -- each only after its predecessor."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            nav_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/target")))
            await pilot.pause(delay=0.05)
            assert backend.writes == [STASH_SEQUENCE]

            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)
            assert backend.writes[0] == STASH_SEQUENCE
            cd_index = next(i for i, w in enumerate(backend.writes) if b"/target" in w)
            assert cd_index == 1

            await recv_q.put(["pre_cmd", "/target"])
            await pilot.pause(delay=0.05)
            assert RESTORE_SEQUENCE not in backend.writes  # not sent until prompt-ready

            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            assert backend.writes[-1] == RESTORE_SEQUENCE

            await recv_q.put(_restore_response(terminal))
            result = await nav_task
            assert result == PurePath("/target")
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_no_overlap_between_transactions() -> None:
    """A second navigation request never starts its own stash until the active one finishes."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/a"))
            await pilot.pause(delay=0.05)
            assert backend.writes.count(STASH_SEQUENCE) == 1

            terminal.request_cd(PurePath("/b"))
            await pilot.pause(delay=0.05)
            assert backend.writes.count(STASH_SEQUENCE) == 1  # second transaction not started yet

            await recv_q.put(_stash_response(terminal, sequence=1))
            await pilot.pause(delay=0.05)
            await recv_q.put(["pre_cmd", "/a"])
            await pilot.pause(delay=0.05)
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            await recv_q.put(_restore_response(terminal, sequence=2))
            await pilot.pause(delay=0.05)

            # The /b transaction now starts.
            assert backend.writes.count(STASH_SEQUENCE) == 2
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_set_terminal_directory_resolves_with_actual_reported_cwd() -> None:
    """The future resolves with the shell's actual reported cwd, not necessarily the requested path."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            nav_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/target")))
            await pilot.pause(delay=0.05)
            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)

            # The shell reports the resolved real path, which differs from the requested path.
            await recv_q.put(["pre_cmd", "/real-target"])
            await pilot.pause(delay=0.05)
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            await recv_q.put(_restore_response(terminal))

            result = await nav_task
            assert result == PurePath("/real-target")
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# Navigation coordinator: hidden output and input preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_navigation_hides_stash_redraw_cd_echo_and_intermediate_output() -> None:
    """stdout produced before the matching NN cwd notification never reaches pyte."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)
            await recv_q.put(["stdout", "STASH_REDRAW"])
            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)
            await recv_q.put(["stdout", " cd /target\r\n"])  # cd echo
            await pilot.pause(delay=0.05)

            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "STASH_REDRAW" not in rendered
            assert "cd /target" not in rendered
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_retains_stdout_after_matching_pre_cmd_until_restore_ack() -> None:
    """stdout arriving between the matching cwd and the restore acknowledgement is retained
    rather than fed to pyte, and is fed once the restore acknowledgement arrives."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)
            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)

            await recv_q.put(["pre_cmd", "/target"])
            await pilot.pause(delay=0.05)
            await recv_q.put(["stdout", "\r\n/target $ "])  # new prompt redraw
            await pilot.pause(delay=0.05)

            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "/target $" not in rendered  # retained, not yet fed

            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            await recv_q.put(_restore_response(terminal))
            await pilot.pause(delay=0.05)

            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "/target $" in rendered
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_restore_clears_old_line_in_place() -> None:
    """On restore acknowledgement, the old editable line is cleared before the retained
    final prompt is fed, so the new prompt overwrites the old one in place."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["stdout", "oldprompt$ "])
            await pilot.pause(delay=0.05)

            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)
            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)
            await recv_q.put(["pre_cmd", "/target"])
            await pilot.pause(delay=0.05)
            await recv_q.put(["stdout", "newprompt$ "])
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            await recv_q.put(_restore_response(terminal))
            await pilot.pause(delay=0.05)

            rendered_lines = [line.plain for line in terminal._display.lines]
            assert not any("oldprompt" in line for line in rendered_lines)
            assert any(line.strip() == "newprompt$" for line in rendered_lines)
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_stdout_after_restore_ack_flows_normally() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)
            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)
            await recv_q.put(["pre_cmd", "/target"])
            await pilot.pause(delay=0.05)
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            await recv_q.put(_restore_response(terminal))
            await pilot.pause(delay=0.05)

            assert terminal._draining is False

            await recv_q.put(["stdout", "typed-by-user"])
            await pilot.pause(delay=0.1)

            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "typed-by-user" in rendered
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# Navigation coordinator: rapid navigation, failures, and disconnect cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rapid_navigation_coalesces_to_newest_queued_request() -> None:
    """When /a is active and /b then /c arrive, /a completes, /b is skipped, then /c runs."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/a"))
            await pilot.pause(delay=0.05)
            terminal.request_cd(PurePath("/b"))
            terminal.request_cd(PurePath("/c"))
            await pilot.pause(delay=0.05)

            assert terminal._nav_queued is not None
            assert terminal._nav_queued.path == PurePath("/c")

            # Complete the active transaction for /a.
            await recv_q.put(_stash_response(terminal, sequence=1))
            await pilot.pause(delay=0.05)
            await recv_q.put(["pre_cmd", "/a"])
            await pilot.pause(delay=0.05)
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            await recv_q.put(_restore_response(terminal, sequence=2))
            await pilot.pause(delay=0.05)

            assert app.path_changed_events[-1].cwd == PurePath("/a")

            # /c's transaction now starts; /b was skipped entirely.
            await recv_q.put(_stash_response(terminal, sequence=3))
            await pilot.pause(delay=0.05)
            await recv_q.put(["pre_cmd", "/c"])
            await pilot.pause(delay=0.05)
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            await recv_q.put(_restore_response(terminal, sequence=4))
            await pilot.pause(delay=0.05)

            cwd_events = [e.cwd for e in app.path_changed_events]
            assert cwd_events == [PurePath("/a"), PurePath("/c")]
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_stash_timeout_sends_no_cd_and_posts_failure_and_stops_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[Terminal.NavigationFailed] = []

    class FailureCapturingApp(TerminalTestApp):
        def on_terminal_navigation_failed(self, event: Terminal.NavigationFailed) -> None:
            received.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = FailureCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True
            monkeypatch.setattr("nova_navigator.terminal.terminal._EDITOR_RESPONSE_TIMEOUT", 0.01)

            nav_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/target")))
            await pilot.pause(delay=0.1)

            await nav_task  # stash ack never arrives -> the stash request times out

            assert not any(b"/target" in w for w in backend.writes)
            assert terminal._draining is False
            assert len(received) == 1
            assert received[0].path == PurePath("/target")
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_failure_after_stash_attempts_restore_before_clearing_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[Terminal.NavigationFailed] = []

    class FailureCapturingApp(TerminalTestApp):
        def on_terminal_navigation_failed(self, event: Terminal.NavigationFailed) -> None:
            received.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = FailureCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True
            monkeypatch.setattr("nova_navigator.terminal.terminal._NAVIGATION_STAGE_TIMEOUT", 0.01)
            monkeypatch.setattr("nova_navigator.terminal.terminal._EDITOR_RESPONSE_TIMEOUT", 0.01)

            nav_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/target")))
            await asyncio.sleep(0)  # let the transaction send the stash request
            await recv_q.put(_stash_response(terminal))

            # No matching cwd notification ever arrives, so the cd wait times out.
            await nav_task
            await pilot.pause(delay=0.05)

            assert RESTORE_SEQUENCE in backend.writes  # best-effort restore was attempted
            assert terminal._draining is False
            assert len(received) == 1
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_third_party_osc7_does_not_advance_transaction() -> None:
    """A non-NN OSC 7 (from_nn=False) arriving mid-transaction is not the matching cwd."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)
            await recv_q.put(_stash_response(terminal))
            await pilot.pause(delay=0.05)

            # Third-party chpwd hook fires first -- must be ignored for the transaction.
            await recv_q.put(["pre_cmd", "/target", False])
            await pilot.pause(delay=0.05)
            assert RESTORE_SEQUENCE not in backend.writes

            # The NN precmd hook fires next -- this is what advances the transaction.
            await recv_q.put(["pre_cmd", "/target", True])
            await pilot.pause(delay=0.05)
            await recv_q.put(["prompt_ready"])
            await pilot.pause(delay=0.05)
            assert backend.writes[-1] == RESTORE_SEQUENCE
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_stale_editor_response_does_not_advance_transaction() -> None:
    """An editor response with a stale sequence number is not treated as the stash ack."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True

            terminal.request_cd(PurePath("/target"))
            await pilot.pause(delay=0.05)

            # Stale response left over from an earlier, unrelated request.
            await recv_q.put(_stash_response(terminal, sequence=99))
            await pilot.pause(delay=0.05)

            assert not any(b"/target" in w for w in backend.writes)
            assert terminal._editor_available is False  # correlation failure disables the protocol
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_navigation_fails_immediately_when_editor_unavailable() -> None:
    """Without editor capability, no navigation writes are ever sent and the request fails safely."""
    received: list[Terminal.NavigationFailed] = []

    class FailureCapturingApp(TerminalTestApp):
        def on_terminal_navigation_failed(self, event: Terminal.NavigationFailed) -> None:
            received.append(event)

    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=FallbackDriver())
    app = FailureCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True

        terminal.request_cd(PurePath("/target"))
        await asyncio.sleep(0.02)

        assert len(backend.writes) == 0
        assert terminal._draining is False
        assert len(received) == 1


@pytest.mark.asyncio
async def test_stop_fails_active_and_queued_navigation_futures() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        terminal._started = True
        terminal._editor_available = True
        terminal._at_prompt = True

        active_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/a")))
        await asyncio.sleep(0)
        queued_task = asyncio.create_task(terminal.set_terminal_directory(PurePath("/b")))
        await asyncio.sleep(0)

        terminal.stop()

        active_result = await active_task
        queued_result = await queued_task
        assert active_result == PurePath("/a")
        assert queued_result == PurePath("/b")
        assert terminal._nav_task is None


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
        terminal._cwd = PurePath("/current/path")
        terminal.request_cd(PurePath("/current/path"))
        assert len(backend.writes) == 0


class PathChangedCapturingApp(TerminalTestApp):
    def __init__(self, terminal: Terminal) -> None:
        super().__init__(terminal)
        self.path_changed_events: list[Terminal.PathChanged] = []

    def on_terminal_path_changed(self, event: Terminal.PathChanged) -> None:
        self.path_changed_events.append(event)


@pytest.mark.asyncio
async def test_request_cd_same_path_short_circuits() -> None:
    """When path == _cwd, nothing is written to the backend."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal._started = True
        terminal._cwd = PurePath("/current/dir")
        initial_write_count = len(backend.writes)

        terminal.request_cd(PurePath("/current/dir"))
        await asyncio.sleep(0)

        assert len(backend.writes) == initial_write_count  # nothing written


@pytest.mark.asyncio
async def test_path_changed_event_has_no_panel_id() -> None:
    """PathChanged event must not have a panel_id attribute (removed in no-SIGSTOP redesign)."""
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
            await recv_q.put(["pre_cmd", "/home/user\n", True])
            await pilot.pause(delay=0.15)

            assert len(received) == 1
            assert received[0].user_initiated is True
            assert not hasattr(received[0], "panel_id")
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# _command_owner: carried through PathChanged and cleared after one command cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_precmd_carries_and_clears_command_owner() -> None:
    """A user-initiated precmd carries the recorded command owner, then clears it."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._cwd = PurePath("/original")
            terminal._command_owner = "left"

            await recv_q.put(["pre_cmd", "/target", True])
            await pilot.pause(delay=0.1)

            assert len(app.path_changed_events) == 1
            assert app.path_changed_events[0].owner == "left"
            assert terminal._command_owner is None
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_user_precmd_without_cwd_change_still_clears_command_owner() -> None:
    """Command completion with no CWD change still clears the recorded owner."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._cwd = PurePath("/same")
            terminal._command_owner = "left"

            await recv_q.put(["pre_cmd", "/same", True])
            await pilot.pause(delay=0.1)

            assert len(app.path_changed_events) == 0
            assert terminal._command_owner is None
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_programmatic_precmd_does_not_consume_command_owner() -> None:
    """A programmatic (navigation-transaction) precmd leaves a recorded owner untouched."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._cwd = PurePath("/original")
            terminal._command_owner = "left"
            terminal._nav_active = _NavigationRequest(path=PurePath("/target"), owner=None, futures=[])
            terminal._nav_waiting_for_cwd = True
            terminal._nav_wait_pre_cmd_future = asyncio.get_running_loop().create_future()

            await recv_q.put(["pre_cmd", "/target", True])
            await pilot.pause(delay=0.1)

            assert len(app.path_changed_events) == 1
            assert app.path_changed_events[0].user_initiated is False
            assert app.path_changed_events[0].owner is None
            assert terminal._command_owner == "left"
        finally:
            terminal._nav_active = None
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_stop_clears_command_owner() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        await asyncio.sleep(0.01)
        terminal._command_owner = "left"

        terminal.stop()

        assert terminal._command_owner is None


@pytest.mark.asyncio
async def test_disconnect_clears_command_owner() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver(), keep_alive=False)
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._command_owner = "left"

            await recv_q.put(["disconnect"])
            await pilot.pause(delay=0.1)

            assert terminal._command_owner is None
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_editor_protocol_timeout_clears_command_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabling the editor protocol after a response timeout clears any recorded owner."""
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._editor_available = True
            terminal._at_prompt = True
            terminal._command_owner = "left"
            monkeypatch.setattr("nova_navigator.terminal.terminal._EDITOR_RESPONSE_TIMEOUT", 0.01)

            assert await terminal.submit_enter(owner="right") is True
            assert terminal._command_owner is None
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
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
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
async def test_third_party_chpwd_osc7_does_not_update_cwd_or_post_path_changed() -> None:
    """Non-NN OSC 7 (from_nn=False) must not update _cwd or post PathChanged.

    Third-party zsh chpwd hooks (oh-my-zsh, powerlevel10k, etc.) emit OSC 7 without
    the panel= prefix, and must never be mistaken for Nova Navigator's own hook.
    """
    backend = FakePtyBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=ZshDriver())
    app = PathChangedCapturingApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._cwd = PurePath("/original")

            await recv_q.put(["pre_cmd", "/other", False])
            await pilot.pause(delay=0.1)

            assert terminal._cwd == PurePath("/original")
            assert len(app.path_changed_events) == 0

            await recv_q.put(["pre_cmd", "/other", True])
            await pilot.pause(delay=0.1)

            assert terminal._cwd == PurePath("/other")
            assert len(app.path_changed_events) == 1
        finally:
            await _stop_recv_only(terminal)
