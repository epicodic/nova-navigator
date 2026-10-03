"""The bars of the editor screen: goto, save as, save progress and the key driven confirm bar."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input, Static

from .core.byte_source import ChangeKind
from .widget import NovaTextArea


@dataclass
class GotoTarget:
    """Parsed goto target: line number or byte offset."""

    kind: Literal["line", "byte"]
    value: int


def parse_goto(text: str) -> GotoTarget | None:
    """Parse goto input: 'N' for line, '@N' for byte offset.

    Args:
        text: Input text (stripped of surrounding whitespace).

    Returns:
        GotoTarget if valid, None otherwise.
    """
    text = text.strip()
    if not text:
        return None

    if text.startswith("@"):
        try:
            offset = int(text[1:])
            if offset < 0:
                return None
            return GotoTarget("byte", offset)
        except ValueError:
            return None

    try:
        line_num = int(text)
        if line_num < 0:
            return None
        return GotoTarget("line", line_num)
    except ValueError:
        return None


class GotoBar(Input):
    """Input field for goto line/byte navigation. Escape closes it."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]

    def __init__(self) -> None:
        super().__init__(id="goto_bar", placeholder="Line or @byte")

    def action_close(self) -> None:
        """Hide the bar, clear it and give the focus back to the editor."""
        self.display = False
        self.value = ""
        editors = self.app.query(NovaTextArea)
        if editors:
            editors.first().focus()


class PathBar(Input):
    """Input field for the save-as path (F2). Enter saves, Escape closes it."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]

    def __init__(self) -> None:
        super().__init__(id="path_bar", placeholder="Save as: path")

    def open(self, path: Path | None) -> None:
        """Show the bar prefilled with `path` and focus it."""
        self.value = "" if path is None else str(path)
        self.display = True
        self.focus()
        self.cursor_position = len(self.value)

    def action_close(self) -> None:
        """Hide the bar, clear it and give the focus back to the editor."""
        self.display = False
        self.value = ""
        editors = self.app.query(NovaTextArea)
        if editors:
            editors.first().focus()


_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")
_KIB = 1024


def format_sizes(done: int, total: int) -> str:
    """Format `done / total` in the binary unit that suits `total` (`1.2 / 5.0 GiB`)."""
    unit = 0
    while unit < len(_UNITS) - 1 and total >= _KIB ** (unit + 1):
        unit += 1
    scale = _KIB**unit
    if unit == 0:
        return f"{done} / {total} B"
    return f"{done / scale:.1f} / {total / scale:.1f} {_UNITS[unit]}"


class SaveBar(Static):
    """Status line of a save: progress while it runs, then a result line for 4 s; a failure stays until a key is pressed. Hidden when idle."""

    RESULT_SECONDS: ClassVar[float] = 4.0

    def __init__(self) -> None:
        super().__init__("", id="save_bar", markup=False)
        self.line = ""
        """The text shown (empty when idle)."""
        self.clock: Callable[[], float] = time.monotonic
        """Time source of the result timeout (tests replace it)."""
        self.failed = False
        """Whether the line is a failure, which stays until a key is pressed."""
        self._expires: float | None = None

    def on_mount(self) -> None:
        self.set_interval(0.1, self.tick)

    def _show(self, line: str) -> None:
        self.line = line
        self.update(line)
        self.display = True

    def show_progress(self, phase: str, done: int, total: int) -> None:
        """Show the state of a running save."""
        self.failed = False
        self._expires = None
        if phase == "flushing":
            self._show("Flushing")
        elif phase == "history":
            self._show("Preserving undo history")
        elif phase == "finishing":
            self._show("Finishing")
        else:
            percent = 100 if total <= 0 else round(done * 100 / total)
            self._show(f"Saving  {format_sizes(done, total)}  {percent} %  Esc cancels")

    def show_result(self, line: str) -> None:
        """Show a result line that disappears after `RESULT_SECONDS`."""
        self.failed = False
        self._expires = self.clock() + self.RESULT_SECONDS
        self._show(line)

    def show_failure(self, line: str) -> None:
        """Show a failure; it stays until `clear`."""
        self.failed = True
        self._expires = None
        self._show(line)

    def clear(self) -> None:
        """Hide the bar."""
        self.failed = False
        self._expires = None
        self.line = ""
        self.update("")
        self.display = False

    def tick(self) -> None:
        """Hide a result line whose time is over."""
        if self._expires is not None and self.clock() >= self._expires:
            self.clear()


_CHANGE_TEXT: dict[ChangeKind, str] = {
    ChangeKind.MODIFIED: "The file changed on disk",
    ChangeKind.TRUNCATED: "The file was truncated on disk",
    ChangeKind.REPLACED: "The file was replaced on disk",
    ChangeKind.DELETED: "The file was deleted on disk",
    ChangeKind.CREATED: "The file was created since the editor started",
    ChangeKind.EXISTS: "The file already exists",
    ChangeKind.UNREADABLE: "The file cannot be read",
}


class ConfirmBar(Static):
    """Key driven question for overwrite, reload, external change and quit: O overwrite, A save as, R reload, Q quit, Esc keep."""

    can_focus = True

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("o,O", "choose('overwrite')", "Overwrite", show=False),
        Binding("a,A", "choose('save_as')", "Save as", show=False),
        Binding("r,R", "choose('reload')", "Reload", show=False),
        Binding("q,Q", "choose('quit')", "Quit", show=False),
        Binding("escape", "choose('keep')", "Keep", show=False),
    ]

    @dataclass
    class Chosen(Message):
        """The user answered the question."""

        choice: str
        """`overwrite`, `save_as`, `reload`, `quit` or `keep`."""
        path: Path | None
        """The file the question was about."""

    def __init__(self) -> None:
        super().__init__("", id="confirm_bar", markup=False)
        self.kind: ChangeKind | None = None
        """What the question is about (`None` for a plain reload)."""
        self.path: Path | None = None
        """The file the question is about."""
        self._allowed: set[str] = set()

    def ask(self, kind: ChangeKind | None, path: Path | None, *, overwrite: bool, save_as: bool, reload: bool) -> None:
        """Show the question and take the focus.

        Args:
            kind: What differs; `None` asks whether to discard the edits and reload.
            path: The file the question is about.
            overwrite: Offer `O`.
            save_as: Offer `A`.
            reload: Offer `R`.
        """
        self.kind = kind
        self.path = path
        self._allowed = {"keep"}
        offers = []
        if overwrite:
            self._allowed.add("overwrite")
            offers.append("O overwrite")
        if save_as:
            self._allowed.add("save_as")
            offers.append("A save as")
        if reload:
            self._allowed.add("reload")
            offers.append("R reload")
        offers.append("Esc keep")
        head = "Discard the edits and reload?" if kind is None else _CHANGE_TEXT.get(kind, "The file changed")
        self.update(f"{head}  {'  '.join(offers)}")
        self.display = True
        self.focus()

    def ask_quit(self, *, save_running: bool) -> None:
        """Ask whether to quit: while a save still runs, or with unsaved changes (replaces any open question)."""
        self.kind = None
        self.path = None
        self._allowed = {"quit", "keep"}
        head = "A save is still running." if save_running else "Discard the unsaved changes and quit?"
        tail = "Q quit anyway  Esc stay" if save_running else "Q quit  Esc stay"
        self.update(f"{head}  {tail}")
        self.display = True
        self.focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Offer only the keys that the question lists."""
        if action == "choose":
            return str(parameters[0]) in self._allowed
        return super().check_action(action, parameters)

    def action_choose(self, choice: str) -> None:
        """Hide the bar and report the answer."""
        self.display = False
        self.post_message(self.Chosen(choice, self.path))
