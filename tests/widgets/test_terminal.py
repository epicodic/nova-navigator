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
