"""Unit tests for the Terminal widget and related utilities in widgets/terminal.py."""

from __future__ import annotations

import asyncio
import contextlib
import shlex
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

from nova_navigator.widgets.terminal import (
    Terminal,
    TerminalDisplay,
    TerminalPyteScreen,
    _translate_terminal_color,
    shell_clear_prompt,
    shell_cmd_cd,
    shell_init_code,
)

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
# shell_init_code
# ---------------------------------------------------------------------------


def test_shell_init_code_embeds_fd_number() -> None:
    code = shell_init_code(7)
    assert ">&7" in code


def test_shell_init_code_defines_nn_precmd_function() -> None:
    code = shell_init_code(3)
    assert "_nn_precmd" in code


def test_shell_init_code_appends_to_precmd_functions() -> None:
    code = shell_init_code(3)
    assert "precmd_functions" in code


def test_shell_init_code_ends_with_newline() -> None:
    code = shell_init_code(1)
    assert code.endswith("\n")


# ---------------------------------------------------------------------------
# shell_clear_prompt
# ---------------------------------------------------------------------------


def test_shell_clear_prompt_length_is_200() -> None:
    assert len(shell_clear_prompt()) == 200


def test_shell_clear_prompt_contains_only_backspaces() -> None:
    result = shell_clear_prompt()
    assert all(c == "\b" for c in result)


# ---------------------------------------------------------------------------
# shell_cmd_cd
# ---------------------------------------------------------------------------


def test_shell_cmd_cd_starts_with_cd() -> None:
    cmd = shell_cmd_cd(PurePath("/home/user"))
    assert cmd.startswith("cd ")


def test_shell_cmd_cd_includes_path() -> None:
    cmd = shell_cmd_cd(PurePath("/home/user/projects"))
    assert "/home/user/projects" in cmd


def test_shell_cmd_cd_redirects_to_dev_null() -> None:
    cmd = shell_cmd_cd(PurePath("/var"))
    assert "/dev/null" in cmd


def test_shell_cmd_cd_contains_conditional_and() -> None:
    # cd failure must not execute the printf
    cmd = shell_cmd_cd(PurePath("/var"))
    assert "&&" in cmd


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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        terminal.stop()
        assert terminal._started is False


@pytest.mark.asyncio
async def test_terminal_start_is_idempotent() -> None:
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        terminal.stop()
        display = terminal.render()
        assert isinstance(display, TerminalDisplay)
        assert len(display.lines) == 1


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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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


# ---------------------------------------------------------------------------
# recv() behavior: stdout updates the display
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdout_message_updates_display_content() -> None:
    terminal = Terminal("/bin/sh")
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
async def test_pre_cmd_message_posts_terminal_pre_cmd_event() -> None:
    received: list[Terminal.PreCmd] = []

    class CapturingApp(TerminalTestApp):
        def on_terminal_pre_cmd(self, event: Terminal.PreCmd) -> None:
            received.append(event)

    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
# shell_cmd_cd — path injection safety
# ---------------------------------------------------------------------------


def test_shell_cmd_cd_handles_path_with_single_quote() -> None:
    path = PurePath("/home/user/O'Brien")
    cmd = shell_cmd_cd(path)
    cd_part = cmd.split("&&")[0].strip()
    # shlex.split must succeed (no unmatched quotes) and yield the original path
    parsed = shlex.split(cd_part)
    assert parsed[0] == "cd"
    assert parsed[1] == str(path)


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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
        await terminal.on_mouse_scroll_down(
            events.MouseScrollDown(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False)
        )
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_on_scroll_down_ignored_when_mouse_tracking_disabled() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = False

        await terminal.on_mouse_scroll_down(
            events.MouseScrollDown(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False)
        )

        assert terminal.send_queue.empty()


@pytest.mark.asyncio
async def test_on_scroll_down_puts_scroll_message_in_send_queue() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_mouse_scroll_down(
            events.MouseScrollDown(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False)
        )

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "scroll"
        assert item[1] == "down"


@pytest.mark.asyncio
async def test_on_scroll_up_puts_scroll_message_in_send_queue() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_mouse_scroll_up(
            events.MouseScrollUp(None, 5, 3, 0, 0, 1, shift=False, meta=False, ctrl=False)
        )

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "scroll"
        assert item[1] == "up"


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
async def test_send_puts_stdin_message_in_send_queue() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        await terminal.send("hello")

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item == ["stdin", "hello"]


# ---------------------------------------------------------------------------
# recv() behavior: extended mouse tracking modes (1002 / 1003 / 1006)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decset_1002h_enables_mouse_tracking() -> None:
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    terminal = Terminal("/bin/sh")
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
    does not change.  When pre_cmd fires, _draining is cleared and the content
    that arrived during draining is absent from the screen."""
    terminal = Terminal("/bin/sh")
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

            # pre_cmd fires: _draining cleared, no screen reset
            await recv_q.put(["pre_cmd", "/some/path\n"])
            await pilot.pause(delay=0.1)
            assert terminal._draining is False
            rendered = "".join(line.plain for line in terminal._display.lines)
            assert "SILENT_CONTENT" not in rendered
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_draining_flag_set_by_send_silent() -> None:
    """send with mode='silent' sets _draining to True before enqueuing the data."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        try:
            assert terminal._draining is False
            await terminal.send("some command\n", mode="silent")
            assert terminal._draining is True
        finally:
            terminal._started = False
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_normal_send_after_pre_cmd_resets_drain_appears_on_screen() -> None:
    """After pre_cmd clears _draining, subsequent stdout is fed to pyte and rendered.
    Stdout that arrived while draining is discarded and never rendered."""
    terminal = Terminal("/bin/sh")
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


def test_has_input_returns_true_when_cursor_past_prompt_position(terminal_instance: Terminal) -> None:
    terminal_instance._prompt_cursor_x = 5
    terminal_instance._screen.cursor.x = 8
    assert terminal_instance.has_input() is True


def test_has_input_returns_false_on_fresh_terminal(terminal_instance: Terminal) -> None:
    # Both _prompt_cursor_x and screen cursor start at 0
    assert terminal_instance.has_input() is False


@pytest.mark.asyncio
async def test_has_input_false_after_pre_cmd_and_display_rebuild() -> None:
    """After a pre_cmd + prompt stdout sequence, has_input() must return False."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["pre_cmd", "/home/user\n"])
            await recv_q.put(["stdout", "$ "])  # prompt drawn; cursor sits right after it
            await pilot.pause(delay=0.15)
            assert terminal.has_input() is False
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_has_input_true_after_prompt_and_user_input() -> None:
    """After prompt + user input, cursor moves right and has_input() returns True."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["pre_cmd", "/home/user\n"])
            await recv_q.put(["stdout", "$ "])  # prompt
            await pilot.pause(delay=0.15)
            await recv_q.put(["stdout", "ls"])  # user typed "ls"
            await pilot.pause(delay=0.15)
            assert terminal.has_input() is True
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# set_terminal_directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_terminal_directory_sends_kill_line_and_cd_silently() -> None:
    """set_terminal_directory enqueues KILL_LINE + cd command and sets _draining."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        await terminal.set_terminal_directory(PurePath("/tmp/test"))  # noqa: S108

        assert terminal._draining is True
        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "stdin"
        data = str(item[1])
        assert data.startswith("\x15")  # KILL_LINE
        assert "/tmp/test" in data  # noqa: S108
        assert data.endswith("\n")


@pytest.mark.asyncio
async def test_set_terminal_directory_no_pending_yank_when_no_input() -> None:
    """If the cursor is at the prompt position, no yank should be scheduled."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal._prompt_cursor_x = 0
        terminal._screen.cursor.x = 0  # no user input

        await terminal.set_terminal_directory(PurePath("/tmp"))  # noqa: S108

        assert terminal._pending_yank is False


@pytest.mark.asyncio
async def test_set_terminal_directory_sets_pending_yank_when_input_present() -> None:
    """If the user has typed text, _pending_yank must be set so it is restored later."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal._prompt_cursor_x = 2
        terminal._screen.cursor.x = 6  # user has typed 4 chars

        await terminal.set_terminal_directory(PurePath("/tmp"))  # noqa: S108

        assert terminal._pending_yank is True


# ---------------------------------------------------------------------------
# Yank mechanism: _rebuild_display triggers yank after prompt snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yank_and_end_of_line_sent_after_prompt_rebuild() -> None:
    """When _pending_yank is True, the first _rebuild_display after a pre_cmd
    must enqueue YANK + END_OF_LINE and clear _pending_yank."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        terminal.send_queue = asyncio.Queue()
        try:
            terminal._pending_yank = True
            terminal._snapshot_prompt_cursor = True  # simulate pre_cmd already fired

            # trigger a display rebuild with prompt stdout
            await recv_q.put(["stdout", "$ "])
            await pilot.pause(delay=0.15)

            assert terminal._pending_yank is False
            assert terminal.send_queue.qsize() == 1
            item = terminal.send_queue.get_nowait()
            assert item[0] == "stdin"
            data = str(item[1])
            assert "\x19" in data  # YANK
            assert "\x05" in data  # END_OF_LINE
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_yank_not_sent_when_pending_yank_is_false() -> None:
    """When _pending_yank is False, no yank must be enqueued after the prompt rebuild."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        terminal.send_queue = asyncio.Queue()
        try:
            terminal._pending_yank = False
            terminal._snapshot_prompt_cursor = True

            await recv_q.put(["stdout", "$ "])
            await pilot.pause(delay=0.15)

            assert terminal.send_queue.empty()
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_prompt_cursor_x_snapshotted_at_rebuild_not_at_pre_cmd() -> None:
    """_prompt_cursor_x must be set during _rebuild_display (after prompt is drawn),
    not immediately when pre_cmd fires."""
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            await recv_q.put(["pre_cmd", "/home/user\n"])
            # At this point the flag is set but no rebuild has happened yet.
            # cursor is still at 0 so _prompt_cursor_x should not have moved.
            await asyncio.sleep(0.005)
            assert terminal._prompt_cursor_x == 0

            # Now send prompt text and wait for the rebuild.
            await recv_q.put(["stdout", "$ "])
            await pilot.pause(delay=0.15)
            # After rebuild the snapshot should reflect the cursor after the prompt.
            assert terminal._prompt_cursor_x == terminal._screen.cursor.x
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# Race condition tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_race_a_stale_pre_cmd_resets_draining_for_current_navigation() -> None:
    """Race A: a pre_cmd already sitting in recv_queue when set_terminal_directory
    sets _draining=True immediately resets the flag back to False, ending silent
    mode before the cd echo has been suppressed.

    Correct behaviour: a pre_cmd that was queued *before* the current navigation
    started should not clear the draining flag that belongs to that navigation.

    This test is expected to FAIL on the current (buggy) code.
    """
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        # Stale pre_cmd from a previous navigation is already in the queue.
        recv_q: asyncio.Queue[list[object]] = asyncio.Queue()
        terminal.recv_queue = recv_q
        await recv_q.put(["pre_cmd", "/old/path\n"])

        # Create the recv() task — it is scheduled but will not run until we
        # yield to the event loop.  send() has no suspension points, so the
        # task cannot interleave with set_terminal_directory.
        terminal.recv_task_t = asyncio.create_task(terminal.recv())

        # This sets _draining=True atomically (no yield inside send()).
        await terminal.set_terminal_directory(PurePath("/new/path"))
        assert terminal._draining is True  # sanity check — draining IS set here

        # Yield to the event loop: recv() runs, processes the stale pre_cmd,
        # and — in the buggy implementation — resets _draining to False.
        await pilot.pause(delay=0.05)

        # _draining should still be True because the pre_cmd predates the current
        # navigation.  On current code this assertion FAILS.
        assert terminal._draining is True  # BUG: currently False

        terminal.recv_task_t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await terminal.recv_task_t


@pytest.mark.asyncio
async def test_race_c_stale_pre_cmd_causes_wrong_prompt_cursor_snapshot() -> None:
    """Race C: in a sequence of two rapid navigations (A then B), A's pre_cmd
    arrives while _draining is still True for B's navigation.

    The stale pre_cmd resets _draining=False and arms _snapshot_prompt_cursor.
    The *first* stdout that arrives (echo of the cd B command) triggers a
    rebuild that consumes the snapshot flag and records the echo cursor position
    as _prompt_cursor_x — not B's real prompt position.

    B's pre_cmd never arrives in this test (dropped / delayed), so the flag is
    never re-armed.  When B's actual prompt stdout arrives, no snapshot is taken.
    _prompt_cursor_x therefore reflects the echo position, not B's prompt.

    A subsequent has_input() call then returns True (falsely detecting user input)
    because cursor.x at B's prompt exceeds the stale _prompt_cursor_x.

    This test is expected to FAIL on the current (buggy) code.
    """
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        try:
            terminal._prompt_cursor_x = 0  # initial: cursor at start of prompt

            # Two rapid navigations — both set _draining=True.
            await terminal.set_terminal_directory(PurePath("/tmp/A"))
            await terminal.set_terminal_directory(PurePath("/tmp/B"))

            # A's pre_cmd arrives (stale):
            #   - resets _draining=False
            #   - arms _snapshot_prompt_cursor=True
            await recv_q.put(["pre_cmd", "/tmp/A\n"])
            await pilot.pause(delay=0.05)
            assert terminal._draining is False
            assert terminal._snapshot_prompt_cursor is True

            # Echo of the cd B command is now visible (draining was reset).
            # Feed "abc" so the cursor lands at column 3 — an intermediate
            # position that is NOT B's real prompt column.
            await recv_q.put(["stdout", "abc"])
            await pilot.pause(delay=0.15)  # wait for the 16.6 ms rebuild timer

            # _snapshot_prompt_cursor was consumed: _prompt_cursor_x is now 3.
            # This is wrong — it reflects the cd echo, not B's prompt.
            assert terminal._snapshot_prompt_cursor is False
            echo_cursor_pos = terminal._prompt_cursor_x  # 3

            # B's pre_cmd never arrives (dropped / delayed — not injected here).
            # B's prompt stdout arrives, moving the cursor to a column past the
            # echo position.  Feed CR+LF then a long prompt so cursor ends at 14.
            await recv_q.put(["stdout", "\r\n/home/user/projects $ "])
            await pilot.pause(delay=0.15)

            # No snapshot: flag is False, so _prompt_cursor_x stays at echo pos.
            assert terminal._snapshot_prompt_cursor is False
            assert terminal._prompt_cursor_x == echo_cursor_pos  # still 3, not 14

            # Cursor is now at column 14 (end of B's prompt).
            prompt_b_cursor = terminal._screen.cursor.x
            assert prompt_b_cursor > echo_cursor_pos  # 14 > 3

            # has_input() compares cursor.x (14) > _prompt_cursor_x (3) → True.
            # The user has NOT typed anything; the correct answer is False.
            # On current code this assertion FAILS.
            assert terminal.has_input() is False  # BUG: currently True
        finally:
            await _stop_recv_only(terminal)
