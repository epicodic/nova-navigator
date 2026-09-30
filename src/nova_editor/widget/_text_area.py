from __future__ import annotations

import dataclasses
import functools
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar, cast

from rich.console import RenderableType
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual._tree_sitter import TREE_SITTER, get_language
from textual.actions import SkipAction
from textual.cache import LRUCache
from textual.color import Color
from textual.content import Content
from textual.expand_tabs import expand_text_tabs_from_widths
from textual.screen import Screen
from textual.style import Style as ContentStyle

from nova_editor.core import ByteSource
from nova_editor.core import SourceChanged as CoreSourceChanged
from nova_editor.core.text_width import utf8_len
from nova_editor.document._cursor_anchor import CursorMachine, CursorState, Op, Verdict
from nova_editor.document._document import (
    Document,
    DocumentBase,
    EditResult,
    Location,
    Selection,
    _utf8_encode,
)
from nova_editor.document._document_navigator import DocumentNavigator
from nova_editor.document._edit import Edit
from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
from nova_editor.document._syntax_aware_document import (
    SyntaxAwareDocument,
    SyntaxAwareDocumentError,
)
from nova_editor.document._wrapped_document import WrappedDocument
from nova_editor.widget._lazy_window import WindowText, section_window, window_text
from nova_editor.widget._long_row_cursor import PROGRESS_BELOW_ONE, LongRowCursor
from nova_editor.widget._text_area_theme import TextAreaTheme

if TYPE_CHECKING:
    from tree_sitter import Language, Query

import textual
from textual import events, log
from textual.binding import Binding
from textual.events import Message, MouseEvent
from textual.geometry import Offset, Region, Size, Spacing, clamp
from textual.reactive import Reactive, reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.timer import Timer

_WORD_WINDOW = 8192
"""Characters scanned on each side of the cursor by the word locators."""

DEFAULT_HIGHLIGHT_LIMIT = 1_048_576
"""Bytes of a lazily opened file up to which syntax highlighting is attempted."""

_DEFAULT_INDENT_WIDTH = 4
"""Default of `NovaTextArea.indent_width`; `open()` gives it to the long-row indexes of the lazy document."""

_PREFIX_CHARS = 1024
"""Characters of a medium or long row that `get_line` returns for a lazy document."""

_ESTIMATE_INTERVAL = 0.25
"""Seconds between size re-estimates while an index of a lazy document grows."""

_MESSAGE_INTERVAL = 0.1
"""Seconds between two `IndexProgress` (and two `JumpProgress`) messages: at most 10 per second."""


def _column_of_byte(text: str, relative: int) -> int:
    """Return the column of the character that holds byte `relative` of `text` (the length of `text` when it lies beyond)."""
    total = 0
    for column, char in enumerate(text):
        total += utf8_len(char)
        if total > relative:
            return column
    return len(text)


@dataclass
class _Jump:
    """A goto request that waits for the scan: a 0-based `row` or an absolute `byte` offset."""

    row: int | None = None
    byte: int | None = None


_PLACEHOLDER_CELL = "\u2591"
"""Fills the part of a window that the scan has not reached yet."""


_GuardedMethod = TypeVar("_GuardedMethod", bound=Callable[..., Any])


def _guard_source(default: object) -> Callable[[_GuardedMethod], _GuardedMethod]:
    """Make a cursor entry point survive `SourceChanged` of a lazy document: the widget fails (see `_fail_source`) and `default` is returned."""

    def decorate(method: _GuardedMethod) -> _GuardedMethod:
        @functools.wraps(method)
        def wrapper(self: NovaTextArea, *args: Any, **kwargs: Any) -> Any:
            try:
                return method(self, *args, **kwargs)
            except CoreSourceChanged as error:
                self._fail_source(str(error))
                return default

        return cast("_GuardedMethod", wrapper)

    return decorate


_OPENING_BRACKETS = {"{": "}", "[": "]", "(": ")"}
_CLOSING_BRACKETS = {v: k for k, v in _OPENING_BRACKETS.items()}
_TREE_SITTER_PATH = Path(textual.__file__).parent / "tree-sitter"
"""The highlight queries ship with the installed textual package (they are not vendored)."""
_HIGHLIGHTS_PATH = _TREE_SITTER_PATH / "highlights/"

StartColumn = int
EndColumn = int | None
HighlightName = str
Highlight = tuple[StartColumn, EndColumn, HighlightName]
"""A tuple representing a syntax highlight within one line."""

BUILTIN_LANGUAGES = [
    "python",
    "markdown",
    "json",
    "toml",
    "yaml",
    "html",
    "css",
    "javascript",
    "rust",
    "go",
    "regex",
    "sql",
    "java",
    "bash",
    "xml",
]
"""Languages that are included in the `syntax` extras."""


class ThemeDoesNotExist(Exception):
    """Raised when the user tries to use a theme which does not exist.
    This means a theme which is not builtin, or has not been registered.
    """


class LanguageDoesNotExist(Exception):
    """Raised when the user tries to use a language which does not exist.
    This means a language which is not builtin, or has not been registered.
    """


@dataclass
class TextAreaLanguage:
    """A container for a language which has been registered with the NovaTextArea."""

    name: str
    """The name of the language"""

    language: Language | None
    """The tree-sitter language object if that has been overridden, or None if it is a built-in language."""

    highlight_query: str
    """The tree-sitter highlight query to use for syntax highlighting."""


class NovaTextArea(ScrollView):
    DEFAULT_CSS: ClassVar[str] = """\
NovaTextArea {
    width: 1fr;
    height: 1fr;
    border: tall $border-blurred;
    padding: 0 1;
    color: $foreground;
    background: $surface;
    pointer: text;
    &.-textual-compact {
        border: none !important;
    }
    & .text-area--cursor {
        text-style: $input-cursor-text-style;
    }
    & .text-area--gutter {
        color: $foreground 40%;
    }

    & .text-area--cursor-gutter {
        color: $foreground 60%;
        background: $boost;
        text-style: bold;
    }

    & .text-area--cursor-line {
       background: $boost;
    }

    & .text-area--selection {
        background: $input-selection-background;
        color: $input-selection-foreground;
    }

    & .text-area--matching-bracket {
        background: $foreground 30%;
    }

    & .text-area--suggestion {
        color: $text-muted;
    }

    & .text-area--placeholder {
        color: $text 40%;
    }

    &:focus {
        border: tall $border;
    }

    &:ansi {
        .text-area--cursor {
            color: $input-cursor-foreground;
            background: $input-cursor-background;
            text-style: reverse;
        }
        & .text-area--selection {
            background: transparent;
            text-style: reverse;
        }
    }

    &:dark {
        .text-area--cursor {
            color: $input-cursor-foreground;
            background: $input-cursor-background;
        }
        &.-read-only .text-area--cursor {
            background: $warning-darken-1;
        }
    }

    &:light {
        .text-area--cursor {
            color: $text 90%;
            background: $foreground 70%;
        }
        &.-read-only .text-area--cursor {
            background: $warning-darken-1;
        }
    }    
}
"""
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "text-area--cursor",
        "text-area--gutter",
        "text-area--cursor-gutter",
        "text-area--cursor-line",
        "text-area--selection",
        "text-area--matching-bracket",
        "text-area--suggestion",
        "text-area--placeholder",
    }
    """
    `NovaTextArea` offers some component classes which can be used to style aspects of the widget.

    Note that any attributes provided in the chosen `TextAreaTheme` will take priority here.

    | Class | Description |
    | :- | :- |
    | `text-area--cursor` | Target the cursor. |
    | `text-area--gutter` | Target the gutter (line number column). |
    | `text-area--cursor-gutter` | Target the gutter area of the line the cursor is on. |
    | `text-area--cursor-line` | Target the line the cursor is on. |
    | `text-area--selection` | Target the current selection. |
    | `text-area--matching-bracket` | Target matching brackets. |
    | `text-area--suggestion` | Target the text set in the `suggestion` reactive. |
    | `text-area--placeholder` | Target the placeholder text. |
    """
    BINDINGS: ClassVar[list] = [
        # Cancel a pending jump (active only while one is pending, see `check_action`)
        Binding("escape", "cancel_pending", show=False),
        # Cursor movement
        Binding(
            "up",
            "cursor_up",
            "Cursor up",
            show=False,
        ),
        Binding(
            "down",
            "cursor_down",
            "Cursor down",
            show=False,
        ),
        Binding(
            "left",
            "cursor_left",
            "Cursor left",
            show=False,
        ),
        Binding(
            "right",
            "cursor_right",
            "Cursor right",
            show=False,
        ),
        Binding(
            "ctrl+left",
            "cursor_word_left",
            "Cursor word left",
            show=False,
        ),
        Binding(
            "ctrl+right",
            "cursor_word_right",
            "Cursor word right",
            show=False,
        ),
        Binding(
            "home,ctrl+a",
            "cursor_line_start",
            "Cursor line start",
            show=False,
        ),
        Binding(
            "end,ctrl+e",
            "cursor_line_end",
            "Cursor line end",
            show=False,
        ),
        Binding(
            "pageup",
            "cursor_page_up",
            "Cursor page up",
            show=False,
        ),
        Binding(
            "pagedown",
            "cursor_page_down",
            "Cursor page down",
            show=False,
        ),
        # Making selections (generally holding the shift key and moving cursor)
        Binding(
            "ctrl+shift+left",
            "cursor_word_left(True)",
            "Cursor left word select",
            show=False,
        ),
        Binding(
            "ctrl+shift+right",
            "cursor_word_right(True)",
            "Cursor right word select",
            show=False,
        ),
        Binding(
            "shift+home",
            "cursor_line_start(True)",
            "Cursor line start select",
            show=False,
        ),
        Binding(
            "shift+end",
            "cursor_line_end(True)",
            "Cursor line end select",
            show=False,
        ),
        Binding(
            "shift+up",
            "cursor_up(True)",
            "Cursor up select",
            show=False,
        ),
        Binding(
            "shift+down",
            "cursor_down(True)",
            "Cursor down select",
            show=False,
        ),
        Binding(
            "shift+left",
            "cursor_left(True)",
            "Cursor left select",
            show=False,
        ),
        Binding(
            "shift+right",
            "cursor_right(True)",
            "Cursor right select",
            show=False,
        ),
        # Shortcut ways of making selections
        # Binding("f5", "select_word", "select word", show=False),
        Binding(
            "f6",
            "select_line",
            "Select line",
            show=False,
        ),
        Binding(
            "f7",
            "select_all",
            "Select all",
            show=False,
        ),
        # Deletion
        Binding(
            "backspace",
            "delete_left",
            "Delete character left",
            show=False,
        ),
        Binding(
            "ctrl+w,ctrl+backspace,alt+backspace",
            "delete_word_left",
            "Delete left to start of word",
            show=False,
        ),
        Binding(
            "delete,ctrl+d",
            "delete_right",
            "Delete character right",
            show=False,
        ),
        Binding(
            "alt+delete",
            "delete_word_right",
            "Delete right to start of word",
            show=False,
        ),
        Binding(
            "ctrl+x,super+x",
            "cut",
            "Cut",
            show=False,
        ),
        Binding(
            "ctrl+c,super+c",
            "copy",
            "Copy",
            show=False,
        ),
        Binding(
            "ctrl+v",
            "paste",
            "Paste",
            show=False,
        ),
        Binding(
            "ctrl+u,super+backspace",
            "delete_to_start_of_line",
            "Delete to line start",
            show=False,
        ),
        Binding(
            "ctrl+k",
            "delete_to_end_of_line_or_delete_line",
            "Delete to line end",
            show=False,
        ),
        Binding(
            "ctrl+shift+k",
            "delete_line",
            "Delete line",
            show=False,
        ),
        Binding(
            "ctrl+z,super+z",
            "undo",
            "Undo",
            show=False,
        ),
        Binding(
            "ctrl+y,super+y",
            "redo",
            "Redo",
            show=False,
        ),
    ]
    """
    | Key(s)                 | Description                                  |
    | :-                     | :-                                           |
    | up                     | Move the cursor up.                          |
    | down                   | Move the cursor down.                        |
    | left                   | Move the cursor left.                        |
    | ctrl+left              | Move the cursor to the start of the word.    |
    | ctrl+shift+left        | Move the cursor to the start of the word and select.    |
    | right                  | Move the cursor right.                       |
    | ctrl+right             | Move the cursor to the end of the word.      |
    | ctrl+shift+right       | Move the cursor to the end of the word and select.      |
    | home,ctrl+a            | Move the cursor to the start of the line.    |
    | end,ctrl+e             | Move the cursor to the end of the line.      |
    | shift+home             | Move the cursor to the start of the line and select.      |
    | shift+end              | Move the cursor to the end of the line and select.      |
    | pageup                 | Move the cursor one page up.                 |
    | pagedown               | Move the cursor one page down.               |
    | shift+up               | Select while moving the cursor up.           |
    | shift+down             | Select while moving the cursor down.         |
    | shift+left             | Select while moving the cursor left.         |
    | shift+right            | Select while moving the cursor right.        |
    | backspace              | Delete character to the left of cursor.      |
    | ctrl+w,ctrl+backspace  | Delete from cursor to start of the word.     |
    | delete,ctrl+d          | Delete character to the right of cursor.     |
    | alt+delete             | Delete from cursor to end of the word.       |
    | ctrl+shift+k           | Delete the current line.                     |
    | ctrl+u,super+backspace | Delete from cursor to the start of the line. |
    | ctrl+k                 | Delete from cursor to the end of the line.   |
    | f6                     | Select the current line.                     |
    | f7                     | Select all text in the document.             |
    | ctrl+z,super+z         | Undo.                                        |
    | ctrl+y,super+y         | Redo.                                        |
    | ctrl+x,super+x         | Cut selection or line if no selection.       |
    | ctrl+c,super+c         | Copy selection to clipboard.                 |
    | ctrl+v,super+v         | Paste from clipboard.                        |
    """

    language: Reactive[str | None] = reactive(None, always_update=True, init=False)
    """The language to use.

    This must be set to a valid, non-None value for syntax highlighting to work.

    If the value is a string, a built-in language parser will be used if available.

    If you wish to use an unsupported language, you'll have to register
    it first using  [`NovaTextArea.register_language`][textual.widgets._text_area.NovaTextArea.register_language].
    """

    theme: Reactive[str] = reactive("css", always_update=True, init=False)
    """The name of the theme to use.

    Themes must be registered using  [`NovaTextArea.register_theme`][textual.widgets._text_area.NovaTextArea.register_theme] before they can be used.

    Syntax highlighting is only possible when the `language` attribute is set.
    """

    selection: Reactive[Selection] = reactive(Selection(), init=False, always_update=True)
    """The selection start and end locations (zero-based line_index, offset).

    This represents the cursor location and the current selection.

    The `Selection.end` always refers to the cursor location.

    If no text is selected, then `Selection.end == Selection.start` is True.

    The text selected in the document is available via the `NovaTextArea.selected_text` property.
    """

    show_line_numbers: Reactive[bool] = reactive(False, init=False)
    """True to show the line number column on the left edge, otherwise False.

    Changing this value will immediately re-render the `NovaTextArea`."""

    line_number_start: Reactive[int] = reactive(1, init=False)
    """The line number the first line should be."""

    indent_width: Reactive[int] = reactive(_DEFAULT_INDENT_WIDTH, init=False)
    """The width of tabs or the multiple of spaces to align to on pressing the `tab` key.

    If the document currently open contains tabs that are currently visible on screen,
    altering this value will immediately change the display width of the visible tabs.
    """

    match_cursor_bracket: Reactive[bool] = reactive(True, init=False)
    """If the cursor is at a bracket, highlight the matching bracket (if found)."""

    cursor_blink: Reactive[bool] = reactive(True, init=False)
    """True if the cursor should blink."""

    soft_wrap: Reactive[bool] = reactive(True, init=False)
    """True if text should soft wrap."""

    pending_progress: Reactive[float | None] = reactive(None, init=False)
    """Fraction in [0, 1) of a deferred cursor jump that the scan has covered; `None` when nothing is pending (lazy documents only)."""

    read_only: Reactive[bool] = reactive(False)
    """True if the content is read-only.

    Read-only means end users cannot insert, delete or replace content.

    The document can still be edited programmatically via the API.
    """

    show_cursor: Reactive[bool] = reactive(True)
    """Show the cursor in read only mode?

    If `True`, the cursor will be visible when `read_only==True`.
    If `False`, the cursor will be hidden when `read_only==True`, and the NovaTextArea will
    scroll like other containers.

    """

    compact: reactive[bool] = reactive(False, toggle_class="-textual-compact")
    """Enable compact display?"""

    highlight_cursor_line: reactive[bool] = reactive(True)
    """Highlight the line under the cursor?"""

    _cursor_visible: Reactive[bool] = reactive(False, repaint=False, init=False)
    """Indicates where the cursor is in the blink cycle. If it's currently
    not visible due to blinking, this is False."""

    suggestion: Reactive[str] = reactive("")
    """A suggestion for auto-complete (pressing right will insert it)."""

    hide_suggestion_on_blur: Reactive[bool] = reactive(True)
    """Hide suggestion when the NovaTextArea does not have focus."""

    placeholder: Reactive[str | Content] = reactive("")
    """Text to show when the text area has no content."""

    @dataclass
    class Changed(Message):
        """Posted when the content inside the NovaTextArea changes.

        Handle this message using the `on` decorator - `@on(NovaTextArea.Changed)`
        or a method named `on_text_area_changed`.
        """

        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            """The `NovaTextArea` that sent this message."""
            return self.text_area

    @dataclass
    class SelectionChanged(Message):
        """Posted when the selection changes.

        This includes when the cursor moves or when text is selected.
        """

        selection: Selection
        """The new selection."""
        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            return self.text_area

    @dataclass
    class SourceChanged(Message):
        """Posted once when the file behind a lazily opened document changed or could not be read; the view stays blank."""

        reason: str
        """What the source reported."""
        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            return self.text_area

    @dataclass
    class JumpProgress(Message):
        """Posted when a deferred cursor jump starts or advances; `fraction` is in [0, 1)."""

        fraction: float
        """How far the scan is towards what the jump waits for."""
        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            return self.text_area

    @dataclass
    class IndexProgress(Message):
        """Posted (at most 10 times per second) while the line scan of a lazy document grows, and once when it completes."""

        count: int
        """Rows found so far: a lower bound until `complete`."""
        complete: bool
        """Whether the line scan is complete, i.e. `count` is exact."""
        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            return self.text_area

    @dataclass
    class IndexingComplete(Message):
        """Posted once when the line scan of a lazy document completed."""

        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            return self.text_area

    @dataclass
    class JumpCompleted(Message):
        """Posted when a goto (or a deferred jump) has moved the cursor; `column` is only an estimate while the cursor is provisional."""

        row: int
        """The row of the cursor (0-based)."""
        column: int
        """The column of the cursor."""
        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            return self.text_area

    @dataclass
    class JumpRejected(Message):
        """Posted when a goto target is out of range; the cursor is unchanged."""

        reason: str
        """Why the request was rejected."""
        text_area: NovaTextArea
        """The `text_area` that sent this message."""

        @property
        def control(self) -> NovaTextArea:
            return self.text_area

    def __init__(
        self,
        text: str = "",
        *,
        language: str | None = None,
        theme: str = "css",
        soft_wrap: bool = False,
        tab_behavior: Literal["focus", "indent"] = "focus",
        read_only: bool = False,
        show_cursor: bool = True,
        show_line_numbers: bool = False,
        line_number_start: int = 1,
        max_checkpoints: int = 50,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: RenderableType | None = None,
        compact: bool = False,
        highlight_cursor_line: bool = True,
        placeholder: str | Content = "",
        _prebuilt_document: LazyDocument | None = None,
    ) -> None:
        """Construct a new `NovaTextArea`.

        Args:
            text: The initial text to load into the NovaTextArea.
            language: The language to use.
            theme: The theme to use.
            soft_wrap: Enable soft wrapping (default False).
            tab_behavior: If 'focus', pressing tab will switch focus. If 'indent', pressing tab will insert a tab.
            read_only: Enable read-only mode. This prevents edits using the keyboard.
            show_cursor: Show the cursor in read only mode (no effect otherwise).
            show_line_numbers: Show line numbers on the left edge.
            line_number_start: What line number to start on.
            max_checkpoints: The maximum number of undo history checkpoints to retain.
            name: The name of the `NovaTextArea` widget.
            id: The ID of the widget, used to refer to it from Textual CSS.
            classes: One or more Textual CSS compatible class names separated by spaces.
            disabled: True if the widget is disabled.
            tooltip: Optional tooltip.
            compact: Enable compact style (without borders).
            highlight_cursor_line: Highlight the line under the cursor.
            placeholder: Text to display when there is not content.
            _prebuilt_document: A lazy document built by `open()`; skips `Document(text)`.
        """
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)

        self._languages: dict[str, TextAreaLanguage] = {}
        """Maps language names to TextAreaLanguage. This is only used for languages
        registered by end-users using `NovaTextArea.register_language`. If a user attempts
        to set `NovaTextArea.language` to a language that is not registered here, we'll
        attempt to get it from the environment. If that fails, we'll fall back to
        plain text.
        """

        self._themes: dict[str, TextAreaTheme] = {}
        """Maps theme names to TextAreaTheme."""

        self.indent_type: Literal["tabs", "spaces"] = "spaces"
        """Whether to indent using tabs or spaces."""

        self._word_pattern = re.compile(r"(?<=\W)(?=\w)|(?<=\w)(?=\W)")
        """Compiled regular expression for what we consider to be a 'word'."""

        self.history: EditHistory = EditHistory(
            max_checkpoints=max_checkpoints,
            checkpoint_timer=2.0,
            checkpoint_max_characters=100,
        )
        """A stack (the end of the list is the top of the stack) for tracking edits."""

        self._selecting = False
        """True if we're currently selecting text using the mouse, otherwise False."""

        self._matching_bracket_location: Location | None = None
        """The location (row, column) of the bracket which matches the bracket the
        cursor is currently at. If the cursor is at a bracket, or there's no matching
        bracket, this will be `None`."""

        self._highlights: dict[int, list[Highlight]] = defaultdict(list)
        """Mapping line numbers to the set of highlights for that line."""

        self._highlight_query: Query | None = None
        """The query that's currently being used for highlighting."""

        self._lazy: LazyDocument | None = _prebuilt_document
        """The lazy document of a widget created by `open()`, else None."""

        self._long_cursor: LongRowCursor | None = LongRowCursor(_prebuilt_document) if _prebuilt_document is not None else None
        """Cursor machine and provisional layout of the current long row (lazy documents only)."""

        self._suppress_scroll = False
        """True while `_reconcile_cursor` sets the exact location: the cursor is not scrolled into view."""

        self._source_failed = False
        """True after `SourceChanged` or after `close()`: a lazy view renders blank rows."""

        self._lazy_closed = False
        self._jump: _Jump | None = None
        """The active goto request that waits for the scan (at most one), else None."""
        self._progress_lock = threading.Lock()
        self._progress_outstanding = False
        """True while a coalesced `_on_index_progress` call is queued for the UI thread (guarded by `_progress_lock`)."""
        self._last_index_message = 0.0
        self._last_jump_message = 0.0
        self._indexing_announced = False
        self._estimate_timer: Timer | None = None
        self._estimating = False
        self._requested_language: str | None = None
        self._highlight_limit = DEFAULT_HIGHLIGHT_LIMIT

        self.document: DocumentBase = _prebuilt_document if _prebuilt_document is not None else Document(text)
        """The document this widget is currently editing."""

        self.wrapped_document: WrappedDocument = LazyWrappedDocument(_prebuilt_document, tab_width=_DEFAULT_INDENT_WIDTH) if _prebuilt_document is not None else WrappedDocument(self.document)
        """The wrapped view of the document."""

        self.navigator: DocumentNavigator = DocumentNavigator(self.wrapped_document)
        """Queried to determine where the cursor should move given a navigation
        action, accounting for wrapping etc."""

        self._cursor_offset = (0, 0)
        """The virtual offset of the cursor (not screen-space offset)."""

        self.set_reactive(NovaTextArea.soft_wrap, soft_wrap)
        self.set_reactive(NovaTextArea.read_only, read_only or _prebuilt_document is not None)
        self.set_reactive(NovaTextArea.show_cursor, show_cursor)
        self.set_reactive(NovaTextArea.show_line_numbers, show_line_numbers)
        self.set_reactive(NovaTextArea.line_number_start, line_number_start)
        self.set_reactive(NovaTextArea.highlight_cursor_line, highlight_cursor_line)
        self.set_reactive(NovaTextArea.placeholder, placeholder)

        self._line_cache: LRUCache[tuple, Strip] = LRUCache(1024)

        self._set_document(text, language)

        self.language = language
        self.theme = theme

        self._theme: TextAreaTheme
        """The `TextAreaTheme` corresponding to the set theme name. When the `theme`
        reactive is set as a string, the watcher will update this attribute to the
        corresponding `TextAreaTheme` object."""

        self.tab_behavior = tab_behavior

        if tooltip is not None:
            self.tooltip = tooltip

        self.compact = compact

    @classmethod
    def open(
        cls,
        source: Path | str | ByteSource,
        *,
        language: str | None = None,
        soft_wrap: bool = False,
        config: LazyConfig | None = None,
        highlight_limit: int = DEFAULT_HIGHLIGHT_LIMIT,
        **kwargs: Any,
    ) -> NovaTextArea:
        """Open a file lazily: a read-only widget whose rows are decoded on demand and whose long rows are only shown through windows.

        The widget owns the source: `close()` (also called on unmount) cancels the scans and closes it.
        The tab width of the long-row indexes is the default `indent_width` (`LazyConfig.tab_width` is replaced by it); changing
        `indent_width` later does not change how long rows are measured.

        Args:
            source: A path or a `ByteSource`.
            language: Language to highlight; used only when the source is at most `highlight_limit` bytes, ignored above it.
            soft_wrap: Start with soft wrapping (default False).
            config: Thresholds of the lazy document.
            highlight_limit: Largest source (bytes) that is highlighted; above it no parser is created and the text stays plain.
            **kwargs: Passed to the constructor (theme, show_line_numbers, ...).

        Returns:
            The widget, ready to be mounted.
        """
        lazy_config = dataclasses.replace(config or LazyConfig(), tab_width=_DEFAULT_INDENT_WIDTH)
        if isinstance(source, str | Path):
            document = LazyDocument.from_path(source, lazy_config)
        else:
            document = LazyDocument(source, lazy_config)
        try:
            area = cls(soft_wrap=soft_wrap, _prebuilt_document=document, **kwargs)
        except BaseException:
            document.close()
            raise
        area._requested_language = language
        area._highlight_limit = highlight_limit
        try:
            area._enable_lazy_highlighting(language, highlight_limit)
        except BaseException:
            document.close()
            raise
        return area

    @property
    def is_lazy(self) -> bool:
        """True when the widget shows a lazily opened, read-only document (`open()`)."""
        return self._lazy is not None

    @property
    def is_estimating(self) -> bool:
        """True while the size re-estimate timer runs, i.e. while an index of a lazy document is still growing."""
        return self._estimating

    def close(self) -> None:
        """Cancel every scan of a lazy document and close its source (joining and closing run on a background thread, see `LazyDocument.close`); idempotent, a no-op for stock documents."""
        lazy = self._lazy
        if lazy is None or self._lazy_closed:
            return
        self._lazy_closed = True
        self._estimating = False
        self._jump = None
        timer = self._estimate_timer
        if timer is not None:
            timer.stop()
        self._line_cache.clear()
        lazy.close()

    def _on_unmount(self) -> None:
        self.close()

    def _fail_source(self, reason: str) -> None:
        """Enter the failed state (blank rows) and post `SourceChanged` once."""
        if self._source_failed:
            return
        self._source_failed = True
        self._line_cache.clear()
        # set_sender: while the screen renders, it is the active pump, and a message whose sender is the parent does not bubble to it.
        self.post_message(self.SourceChanged(reason, self).set_sender(self))
        self.refresh()

    def _ensure_estimating(self) -> None:
        """Resume the size re-estimate timer when an index of a lazy document grows again (a long row was reached)."""
        lazy = self._lazy
        timer = self._estimate_timer
        if lazy is None or timer is None or self._estimating or self._lazy_closed or self._source_failed:
            return
        if self._needs_estimates(lazy):
            self._estimating = True
            timer.resume()

    def _needs_estimates(self, lazy: LazyDocument) -> bool:
        """Whether sizes can still change: an index is growing, or the wrapped estimate still holds rows that a spent budget left estimated."""
        if lazy.is_growing():
            return True
        wrapped = self.wrapped_document
        return isinstance(wrapped, LazyWrappedDocument) and wrapped.pending_refinement

    def _estimate_tick(self) -> None:
        """Re-estimate the virtual size while an index grows; pause the timer after the refresh that follows the last progress."""
        lazy = self._lazy
        if lazy is None or self._lazy_closed or self._source_failed:
            self._estimating = False
            if self._estimate_timer is not None:
                self._estimate_timer.pause()
            return
        growing = self._reestimate(lazy)
        replay = self._reconcile_cursor()
        self._line_cache.clear()
        self.refresh()
        self._schedule_replay(replay)
        self._settle_timer(growing)

    def _settle_timer(self, growing: bool) -> None:
        """Pause the estimate timer once nothing can change any more."""
        if not growing:
            self._estimating = False
            if self._estimate_timer is not None:
                self._estimate_timer.pause()

    def _reestimate(self, lazy: LazyDocument) -> bool:
        """Drop the provisional estimates and refresh the virtual size (no paint); return whether sizes can still change.

        While the cursor is provisional or pending the size refresh must not scroll to its estimated column.
        """
        growing = self._needs_estimates(lazy)
        wrapped = self.wrapped_document
        self._suppress_scroll = self._cursor_unresolved()
        try:
            if isinstance(wrapped, LazyWrappedDocument):
                wrapped.refresh_estimates()
            self._refresh_size()
        except CoreSourceChanged as error:
            self._fail_source(str(error))
        except (RowUnavailable, IndexError):
            pass
        finally:
            self._suppress_scroll = False
        return growing

    def _schedule_replay(self, replay: tuple[Op, bool] | None) -> None:
        """Replay a deferred cursor operation after the next paint, so the resolving frame and the operation are separate frames."""
        if replay is not None:
            self.call_after_refresh(self._replay_open, replay[0], replay[1])

    def _replay_open(self, op: Op, select: bool) -> None:
        """Replay a deferred operation unless the widget was closed or failed in the meantime."""
        if not self._lazy_closed and not self._source_failed:
            self._replay(op, select=select)

    # --- Provisional byte-anchored cursor on long rows (ACT3 design 7)
    @property
    def cursor_state(self) -> CursorState:
        """State of the cursor: RESOLVED (exact column), PROVISIONAL (exact byte, estimated column) or PENDING (a jump waits for the scan).

        Always RESOLVED off long rows and for stock documents.
        """
        machine = self._track_cursor()
        return CursorState.RESOLVED if machine is None else machine.state

    def peek_cursor_state(self) -> tuple[CursorState, int | None]:
        """Return `(state, byte offset within its row of the anchor)` of the current cursor machine without creating or advancing anything.

        Unlike `cursor_state` it never starts a scan: a cursor that has not been tracked yet reports `(RESOLVED, None)`. For probes and benchmarks.
        """
        cursor = self._long_cursor
        machine = None if cursor is None else cursor.machine
        if machine is None:
            return CursorState.RESOLVED, None
        return machine.state, machine.anchor.byte_rel

    @property
    def column_exact(self) -> bool:
        """True when the column of `cursor_location` is exact (it is only an estimate while the cursor is PROVISIONAL)."""
        return self.cursor_state is CursorState.RESOLVED

    @property
    def cursor_byte_offset(self) -> int | None:
        """Byte offset of the cursor from the start of the document; exact in every state (`None` only when unknown)."""
        row, column = self.cursor_location
        lazy = self._lazy
        machine = self._track_cursor()
        if lazy is None or machine is None:
            return self.document.byte_offset(row, column)
        try:
            start = lazy.byte_offset(row, 0)
            if start is None:
                return None
            exact = lazy.byte_offset(row, column) if machine.state is CursorState.RESOLVED else None
            return start + machine.anchor.byte_rel if exact is None else exact
        except CoreSourceChanged as error:
            self._fail_source(str(error))
            return None

    def toggle_wrap(self) -> None:
        """Flip `soft_wrap`; the cursor keeps its byte (a provisional cursor on a long row waits for the scan in wrap mode)."""
        self.soft_wrap = not self.soft_wrap

    def cancel_pending(self) -> None:
        """Drop a pending jump (a goto or a deferred cursor operation).

        A deferred operation leaves the cursor where it is (provisional); a wrap-mode jump returns it to its previous resolved position.

        A cancelled jump never completes later. No-op unless one is pending.
        """
        if self._jump is not None:
            self._jump = None
            self._set_progress(None)
        cursor = self._long_cursor
        machine = None if cursor is None else cursor.machine
        if cursor is None or machine is None or machine.state is not CursorState.PENDING:
            return
        machine.cancel()
        cursor.layout = None
        cursor.pending_target = None
        self._set_progress(None)
        anchor = machine.anchor
        self.selection = Selection.cursor((anchor.row, anchor.column))

    # --- Goto, deferred jumps, index progress (ACT3 design 8, 9)
    @property
    def line_count(self) -> int:
        """Number of rows: a lower bound while `line_count_exact` is False (the line scan of a lazy document is still running)."""
        return self.document.line_count

    @property
    def line_count_exact(self) -> bool:
        """True when `line_count` is exact: always for stock documents, after the line scan completed for lazy ones."""
        lazy = self._lazy
        return lazy is None or lazy.snapshot().complete

    @property
    def indexing_complete(self) -> bool:
        """True when nothing is left to scan for the line count (always True for stock documents)."""
        return self.line_count_exact

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Make `escape` (`cancel_pending`) active only while a jump is pending, so it never shadows another use of the key."""
        if action == "cancel_pending":
            return self._has_pending()
        return super().check_action(action, parameters)

    def action_cancel_pending(self) -> None:
        """Cancel the pending jump (bound to escape)."""
        self.cancel_pending()

    def _cursor_unresolved(self) -> bool:
        """Whether the cursor of a long row is provisional or pending (waits for the scan to resolve it)."""
        cursor = self._long_cursor
        machine = None if cursor is None else cursor.machine
        return machine is not None and machine.state is not CursorState.RESOLVED

    def _has_pending(self) -> bool:
        """Whether a goto or a deferred cursor operation waits for the scan."""
        if self._jump is not None:
            return True
        cursor = self._long_cursor
        machine = None if cursor is None else cursor.machine
        return machine is not None and machine.state is CursorState.PENDING

    def goto_line(self, line: int) -> None:
        """Move the cursor to the start of a line (1-based).

        A line the scan has already passed is reached at once. Otherwise the jump stays pending (`pending_progress` grows, `JumpProgress`
        is posted) and completes when the scan gets there. A line above the final line count, or below 1, is rejected. Posts
        `JumpCompleted` or `JumpRejected`; a new request cancels the pending one.
        """
        self._start_jump(_Jump(row=line - 1) if line >= 1 else None, f"line {line} is not a line number (lines start at 1)")

    def goto_byte(self, offset: int) -> None:
        """Move the cursor to a byte offset of the document.

        An offset inside a multi-byte character lands on that character. Beyond the scanned part the jump stays pending; inside a long
        row the cursor becomes provisional (no wrap) or the jump stays pending (wrap). An offset below 0 or at or above the length is
        rejected. Posts `JumpCompleted` or `JumpRejected`; a new request cancels the pending one.
        """
        self._start_jump(_Jump(byte=offset) if offset >= 0 else None, f"byte offset {offset} is negative")

    def _start_jump(self, jump: _Jump | None, reason: str) -> None:
        """Cancel the pending request, then run `jump` (or reject the request when `jump` is None)."""
        self.cancel_pending()
        if jump is None:
            self._reject(reason)
            return
        self._jump = jump
        self._run_jump(notify=True)

    def _reject(self, reason: str) -> None:
        self.post_message(self.JumpRejected(reason, self).set_sender(self))

    def _run_jump(self, *, notify: bool) -> None:
        """Try the active request: finish it (moved or rejected) or keep it pending with a progress fraction."""
        jump = self._jump
        if jump is None or self._lazy_closed:
            return
        try:
            progress = self._drive_stock(jump) if self._lazy is None else self._drive_lazy(jump)
        except CoreSourceChanged as error:
            self._fail_source(str(error))
            self._reject(str(error))
            progress = None
        except (RowUnavailable, IndexError):
            self._reject("the target row cannot be resolved")
            progress = None
        if progress is None:
            self._jump = None
            self._set_progress(None)
        else:
            self._set_progress(progress, notify=notify)

    def _complete_jump(self) -> None:
        row, column = self.cursor_location
        self.post_message(self.JumpCompleted(row, column, self).set_sender(self))

    def _drive_stock(self, jump: _Jump) -> float | None:
        """Goto on an ordinary document: everything is known, so the request is answered at once."""
        document = self.document
        if jump.row is not None:
            if jump.row >= document.line_count:
                self._reject(f"line {jump.row + 1} is beyond the last line ({document.line_count})")
                return None
            self.move_cursor((jump.row, 0))
            self._complete_jump()
            return None
        offset = jump.byte or 0
        newline = len(document.newline.encode())
        start = 0
        for row in range(document.line_count):
            text = document.get_line(row)
            size = utf8_len(text)
            if offset <= start + size:
                self.move_cursor((row, _column_of_byte(text, offset - start)))
                self._complete_jump()
                return None
            start += size + newline
        self._reject(f"byte offset {offset} is beyond the end of the document")
        return None

    def _drive_lazy(self, jump: _Jump) -> float | None:
        lazy = self._lazy
        assert lazy is not None
        snap = lazy.snapshot()
        if snap.error is not None:
            raise snap.error
        if jump.row is not None:
            return self._drive_line(jump.row, snap.count, complete=snap.complete)
        return self._drive_byte(lazy, jump.byte or 0, snap.scanned_bytes, complete=snap.complete)

    def _drive_line(self, row: int, count: int, *, complete: bool) -> float | None:
        if row < count - 1 or complete:
            if row >= count:
                self._reject(f"line {row + 1} is beyond the last line ({count})")
                return None
            self.move_cursor((row, 0))
            self._complete_jump()
            return None
        return min(count / (row + 1), PROGRESS_BELOW_ONE)

    def _drive_byte(self, lazy: LazyDocument, offset: int, scanned: int, *, complete: bool) -> float | None:
        if offset >= lazy.length:
            self._reject(f"byte offset {offset} is beyond the end of the file ({lazy.length} bytes)")
            return None
        if offset == 0:
            self.move_cursor((0, 0))
            self._complete_jump()
            return None
        if offset >= scanned and not complete:
            return min(scanned / offset, PROGRESS_BELOW_ONE)
        found = lazy.row_at_offset(offset)
        if found is None:
            self._reject(f"byte offset {offset} cannot be resolved")
            return None
        row, span = found
        relative = min(offset - span.start, span.content_end - span.start)
        if lazy.is_long(row):
            return self._drive_long_row_byte(lazy, row, relative)
        column = _column_of_byte(lazy.get_line(row), relative)
        self.move_cursor((row, column))
        self._complete_jump()
        return None

    def _drive_long_row_byte(self, lazy: LazyDocument, row: int, relative: int) -> float | None:
        """Goto a byte of a long row: exact when scanned, PROVISIONAL without wrap, pending with wrap (design 8.1)."""
        index = lazy.anchor_index(row)
        relative = index.align(relative)
        if self.soft_wrap and index.exact_column(relative) is None:
            return min(index.frontier_byte() / max(relative, 1), PROGRESS_BELOW_ONE)
        self.move_cursor((row, 0))
        machine = self._track_cursor()
        if machine is not None:
            machine.jump_to_byte(relative)
            anchor = machine.anchor
            self.selection = Selection.cursor((anchor.row, anchor.column))
            self.record_cursor_width()
        self._complete_jump()
        return None

    def _index_callback(self) -> None:
        """Scan thread: announce progress to the UI thread. Publishes only; at most one call is outstanding (coalescing).

        `post_message` is thread-safe and never waits for the UI thread, so a scan cannot deadlock with `close()` joining it.
        """
        with self._progress_lock:
            if self._progress_outstanding or self._lazy_closed:
                return
            self._progress_outstanding = True
        try:
            posted = self.post_message(events.Callback(self._on_index_progress))
        except RuntimeError:  # the app is closing, or there is no app any more
            posted = False
        if not posted:
            with self._progress_lock:
                self._progress_outstanding = False

    def _on_index_progress(self) -> None:
        """UI thread: react to scan progress (line count, cursor reconcile, pending jump, messages at most 10 per second)."""
        with self._progress_lock:
            self._progress_outstanding = False
        lazy = self._lazy
        if lazy is None or self._lazy_closed or self._source_failed:
            return
        growing = True
        if not self._estimating or self._cursor_unresolved() or self._jump is not None:
            growing = self._reestimate(lazy)  # a scan that ended between two ticks still gets its final size estimate; a waiting cursor needs a fresh one
        replay = self._reconcile_cursor()
        self._line_cache.clear()  # rows painted from a lagging scan (placeholders) are rebuilt; once per callback
        self.refresh()
        self._schedule_replay(replay)
        self._settle_timer(growing)
        self._run_jump(notify=time.monotonic() - self._last_jump_message >= _MESSAGE_INTERVAL)
        snap = lazy.snapshot()
        now = time.monotonic()
        if snap.error is None and snap.complete and not self._indexing_announced:
            self._indexing_announced = True
            self._last_index_message = now
            self.post_message(self.IndexProgress(snap.count, True, self).set_sender(self))
            self.post_message(self.IndexingComplete(self).set_sender(self))
        elif not snap.complete and now - self._last_index_message >= _MESSAGE_INTERVAL:
            self._last_index_message = now
            self.post_message(self.IndexProgress(snap.count, False, self).set_sender(self))

    def _track_cursor(self) -> CursorMachine | None:
        """Return the cursor machine of the current long row (created on entering the row); `None` off long rows."""
        cursor = self._long_cursor
        if cursor is None or self._lazy_closed or self._source_failed:
            return None
        row, column = self.cursor_location
        try:
            return cursor.track(row, column, wrap=self.soft_wrap)
        except CoreSourceChanged as error:
            self._fail_source(str(error))
        except (RowUnavailable, IndexError):
            pass
        cursor.drop()
        return None

    def _set_progress(self, fraction: float | None, *, notify: bool = True) -> None:
        """Publish the progress of a pending jump (`None` clears it) and repaint at once; `notify=False` withholds the message."""
        changed = fraction != self.pending_progress
        self.pending_progress = fraction
        if fraction is not None and changed and notify:
            self._last_jump_message = time.monotonic()
            self.post_message(self.JumpProgress(fraction, self).set_sender(self))
        self.refresh()

    def _end_is_known(self, row: int, column: int) -> bool:
        """Whether End on a long row can be answered by a location: the row end (no wrap) or the end of the section is scanned."""
        if self.navigator.end_is_known(row):
            return True
        wrapped = self.wrapped_document
        if self.soft_wrap and isinstance(wrapped, LazyWrappedDocument):
            return wrapped.section_start(row, wrapped.section_index(row, column) + 1) is not None
        return False

    def _machine_verdict(self, machine: CursorMachine, op: Op) -> Verdict | None:
        """Ask the machine about `op`; `None` means the ordinary (exact) code path must handle it."""
        cursor = self._long_cursor
        index = None if cursor is None else cursor.index
        if index is None:
            return None
        row, column = self.cursor_location
        end_rel = index.row_end_rel
        state = machine.state
        if op is Op.END:
            return None if self._end_is_known(row, column) else machine.jump_to_byte(end_rel)
        if op is Op.LEFT or op is Op.RIGHT:
            edge = machine.anchor.byte_rel <= 0 if op is Op.LEFT else machine.anchor.byte_rel >= end_rel
            return None if edge or (state is CursorState.RESOLVED and index.complete()) else machine.apply(op)
        if op is Op.WORD_LEFT or op is Op.WORD_RIGHT:
            return None if state is CursorState.RESOLVED else machine.apply(op)
        if op is Op.HOME:
            return machine.apply(op) if state is CursorState.PROVISIONAL else None
        return None if state is CursorState.RESOLVED else machine.apply(op)

    def _lazy_move(self, op: Op, *, select: bool = False) -> bool:
        """Route a cursor movement on a long row through the cursor machine; return True when it was handled here.

        Left, right and word moves work on the byte anchor (a word move is a fixed step of `WORD_STEP_CHARS` characters while the
        cursor is not resolved). Up, down and page moves from a PROVISIONAL cursor become PENDING and are replayed when the scan
        resolves the cursor; while PENDING every movement is ignored (Escape or `cancel_pending()` drops the deferred operation and keeps
        the cursor where it is; a wrap-mode jump returns to the previous position).
        """
        cursor = self._long_cursor
        machine = self._track_cursor()
        if cursor is None or machine is None or cursor.index is None:
            return False
        if machine.state is CursorState.PENDING:
            return True
        if not select and not self.selection.is_empty and op in {Op.LEFT, Op.RIGHT}:
            return False
        row = machine.anchor.row
        if op in {Op.RIGHT, Op.WORD_RIGHT} and machine.anchor.byte_rel >= cursor.index.row_end_rel and not self.navigator.end_is_known(row):
            # The end of the row is reached but its length is not known: the next row starts at column 0 without any scan.
            if row + 1 < self.document.line_count:
                self.move_cursor((row + 1, 0), select=select)
            return True
        verdict = self._machine_verdict(machine, op)
        if verdict is None:
            return False
        if verdict is Verdict.PENDING:
            cursor.pending_select = select
            cursor.pending_target = cursor.index.row_end_rel if op is Op.END else machine.anchor.byte_rel
            self._set_progress(cursor.progress())
        elif verdict is Verdict.DONE:
            start, _end = self.selection
            location = (machine.anchor.row, machine.anchor.column)
            self.selection = Selection(start, location) if select else Selection.cursor(location)
            self.record_cursor_width()
        return True

    def _place_provisional(self, scroll_x: int) -> None:
        """Lay out a provisional cursor row after the cursor moved: undo the estimate based scroll, then scroll to the layout.

        Args:
            scroll_x: The horizontal scroll before `scroll_cursor_visible` ran.
        """
        cursor = self._long_cursor
        machine = None if cursor is None else cursor.machine
        if cursor is None or machine is None or machine.state is CursorState.RESOLVED or self.soft_wrap:
            if cursor is not None:
                cursor.layout = None
            return
        if machine.state is CursorState.PENDING and cursor.layout is None:
            return
        self.scroll_to(x=scroll_x, animate=False)
        cx_est = self.wrapped_document.location_to_offset(self.cursor_location).x
        visible = max(1, self.scrollable_content_region.size.width - self.gutter_width)
        layout = cursor.place(machine.anchor.byte_rel, cx_est, visible, self.indent_width, scroll_x)
        if layout is not None:
            self.scroll_to(x=layout.left_x, animate=False)
            cursor.adopt_scroll(self.scroll_offset.x)
        self._recompute_cursor_offset()
        self.app.cursor_position = self.cursor_screen_offset
        self._line_cache.clear()
        self.refresh()

    def _reconcile_cursor(self) -> tuple[Op, bool] | None:
        """Resolve the cursor when the scan has reached it; return the deferred operation `(op, select)` to replay, if any.

        The caller clears the line cache, refreshes once and schedules the replay (`_schedule_replay`).

        A provisional cursor keeps its screen position: the exact location is set without scrolling the cursor into view and
        `scroll_x` becomes the exact display column of the character at the left edge (design 7.3). A deferred operation is
        replayed afterwards, a pending jump moves the cursor to its target.
        """
        cursor = self._long_cursor
        machine = None if cursor is None else cursor.machine
        if cursor is None or machine is None or machine.state is CursorState.RESOLVED or self._lazy_closed or self._source_failed:
            return None
        before = machine.anchor.byte_rel
        keep_view = cursor.layout is not None
        try:
            resolved = machine.on_frontier()
            if not resolved:
                if machine.state is CursorState.PENDING:
                    self._set_progress(cursor.progress())
                return None
            anchor = machine.anchor
            exact_x = cursor.exact_left_x(anchor.row, self.indent_width) if keep_view and anchor.byte_rel == before else None
            cursor.layout = None
            cursor.pending_target = None
            self._suppress_scroll = exact_x is not None
            try:
                start, end = self.selection
                location = (anchor.row, anchor.column)
                self.selection = Selection(start, location) if start != end else Selection.cursor(location)
            finally:
                self._suppress_scroll = False
            if exact_x is not None:
                self.scroll_to(x=exact_x, animate=False)
                self._recompute_cursor_offset()
        except CoreSourceChanged as error:
            self._fail_source(str(error))
            return None
        self._set_progress(None)
        op = machine.take_pending_op()
        return None if op is None else (op, cursor.pending_select)

    def _replay(self, op: Op, *, select: bool) -> None:
        """Perform an operation that was deferred until the cursor resolved."""
        if op is Op.UP:
            self.action_cursor_up(select=select)
        elif op is Op.DOWN:
            self.action_cursor_down(select=select)
        elif op is Op.PAGE_UP:
            self.action_cursor_page_up()
        elif op is Op.PAGE_DOWN:
            self.action_cursor_page_down()

    def _map_cursor_for_wrap(self) -> None:
        """Map the cursor by its byte anchor when `soft_wrap` changed: a provisional cursor waits for the scan in wrap mode."""
        cursor = self._long_cursor
        machine = self._track_cursor()
        if cursor is None or machine is None:
            return
        machine.set_wrap(self.soft_wrap)
        if self.soft_wrap:
            cursor.layout = None
        if machine.state is CursorState.PENDING:
            if cursor.pending_target is None:
                cursor.pending_target = machine.anchor.byte_rel
            self._set_progress(cursor.progress())
        elif machine.state is CursorState.PROVISIONAL:
            cursor.pending_target = None
            self._set_progress(None)
            location = (machine.anchor.row, machine.anchor.column)
            if self.cursor_location != location:
                self.selection = Selection.cursor(location)

    def _after_wrap_change(self) -> None:
        """Scroll the cursor into view once the wrap change is laid out."""
        scroll_x = self.scroll_offset.x
        self.scroll_cursor_visible(center=True)
        self._place_provisional(scroll_x)

    def _mouse_target(self, event: MouseEvent) -> Location | None:
        """Return the document location of a mouse event, or `None` when a click on a long row cannot be placed.

        A long row is never decoded for this. A click is ignored when the scan has not reached the clicked display column (or
        wrapped section), and on a row whose cursor is provisional (its screen columns are then not display columns).
        """
        try:
            return self._covered_mouse_target(event)
        except CoreSourceChanged as error:
            self._fail_source(str(error))
        except (RowUnavailable, IndexError):
            pass
        return None

    def _covered_mouse_target(self, event: MouseEvent) -> Location | None:
        """Return the location under the mouse, `None` when it lies on a long row region that is not scanned or not laid out exactly."""
        lazy = self._lazy
        wrapped = self.wrapped_document
        target = self.get_target_document_location(event)
        if lazy is None or not isinstance(wrapped, LazyWrappedDocument):
            return target
        row = target[0]
        if not lazy.is_long(row):
            return target
        cursor = self._long_cursor
        if cursor is not None and not self.soft_wrap and cursor.provisional_layout(row) is not None:
            return None
        scroll_x, scroll_y = self.scroll_offset
        x = event.x - self.gutter_width + scroll_x - self.gutter.left
        y = event.y + scroll_y - self.gutter.top
        base = 0
        if self.soft_wrap:
            _row, section = wrapped.row_of_y(max(0, y))
            start = wrapped.section_start(row, section) if section else 0
            if start is None:
                return None
            shown = lazy.display_column(row, start, self.indent_width) if section else 0
            if shown is None:
                return None
            base = shown
        if lazy.column_at_display(row, base + max(0, x), self.indent_width) is None:
            return None
        return target

    @classmethod
    def code_editor(
        cls,
        text: str = "",
        *,
        language: str | None = None,
        theme: str = "monokai",
        soft_wrap: bool = False,
        tab_behavior: Literal["focus", "indent"] = "indent",
        read_only: bool = False,
        show_cursor: bool = True,
        show_line_numbers: bool = True,
        line_number_start: int = 1,
        max_checkpoints: int = 50,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: RenderableType | None = None,
        compact: bool = False,
        highlight_cursor_line: bool = True,
        placeholder: str | Content = "",
    ) -> NovaTextArea:
        """Construct a new `NovaTextArea` with sensible defaults for editing code.

        This instantiates a `NovaTextArea` with line numbers enabled, soft wrapping
        disabled, "indent" tab behavior, and the "monokai" theme.

        Args:
            text: The initial text to load into the NovaTextArea.
            language: The language to use.
            theme: The theme to use.
            soft_wrap: Enable soft wrapping (default False).
            tab_behavior: If 'focus', pressing tab will switch focus. If 'indent', pressing tab will insert a tab.
            read_only: Enable read-only mode. This prevents edits using the keyboard.
            show_cursor: Show the cursor in read only mode (no effect otherwise).
            show_line_numbers: Show line numbers on the left edge.
            line_number_start: What line number to start on.
            name: The name of the `NovaTextArea` widget.
            id: The ID of the widget, used to refer to it from Textual CSS.
            classes: One or more Textual CSS compatible class names separated by spaces.
            disabled: True if the widget is disabled.
            tooltip: Optional tooltip
            compact: Enable compact style (without borders).
            highlight_cursor_line: Highlight the line under the cursor.
        """
        return cls(
            text,
            language=language,
            theme=theme,
            soft_wrap=soft_wrap,
            tab_behavior=tab_behavior,
            read_only=read_only,
            show_cursor=show_cursor,
            show_line_numbers=show_line_numbers,
            line_number_start=line_number_start,
            max_checkpoints=max_checkpoints,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            tooltip=tooltip,
            compact=compact,
            highlight_cursor_line=highlight_cursor_line,
            placeholder=placeholder,
        )

    @staticmethod
    def _get_builtin_highlight_query(language_name: str) -> str:
        """Get the highlight query for a builtin language.

        Args:
            language_name: The name of the builtin language.

        Returns:
            The highlight query.
        """
        try:
            highlight_query_path = Path(_HIGHLIGHTS_PATH.resolve()) / f"{language_name}.scm"
            highlight_query = highlight_query_path.read_text()
        except OSError as error:
            log.warning(f"Unable to load highlight query. {error}")
            highlight_query = ""

        return highlight_query

    def notify_style_update(self) -> None:
        self._line_cache.clear()
        super().notify_style_update()

    def update_suggestion(self) -> None:
        """A hook to update the [`suggestion`][textual.widgets.NovaTextArea.suggestion] attribute."""

    def check_consume_key(self, key: str, character: str | None = None) -> bool:
        """Check if the widget may consume the given key.

        As a textarea we are expecting to capture printable keys.

        Args:
            key: A key identifier.
            character: A character associated with the key, or `None` if there isn't one.

        Returns:
            `True` if the widget may capture the key in its `Key` message, or `False` if it won't.
        """
        if self.read_only:
            # In read only mode we don't consume any key events
            return False
        if self.tab_behavior == "indent" and key == "tab":
            # If tab_behavior is indent, then we consume the tab
            return True
        # Otherwise we capture all printable keys
        return character is not None and character.isprintable()

    def _build_highlight_map(self) -> None:
        """Query the tree for ranges to highlights, and update the internal highlights mapping."""
        self._line_cache.clear()
        highlights = self._highlights
        highlights.clear()
        if not self._highlight_query:
            return

        captures = self.document.query_syntax_tree(self._highlight_query)
        for highlight_name, nodes in captures.items():
            for node in nodes:
                node_start_row, node_start_column = node.start_point
                node_end_row, node_end_column = node.end_point

                if node_start_row == node_end_row:
                    highlight = (node_start_column, node_end_column, highlight_name)
                    highlights[node_start_row].append(highlight)
                else:
                    # Add the first line of the node range
                    highlights[node_start_row].append((node_start_column, None, highlight_name))

                    # Add the middle lines - entire row of this node is highlighted
                    for node_row in range(node_start_row + 1, node_end_row):
                        highlights[node_row].append((0, None, highlight_name))

                    # Add the last line of the node range
                    highlights[node_end_row].append((0, node_end_column, highlight_name))

    def _watch_has_focus(self, focus: bool) -> None:
        self._cursor_visible = focus
        if focus:
            self._restart_blink()
            self.app.cursor_position = self.cursor_screen_offset
            self.history.checkpoint()
        else:
            self._pause_blink(visible=False)

    @_guard_source(None)
    def _watch_selection(self, previous_selection: Selection, selection: Selection) -> None:
        """When the cursor moves, scroll it into view."""
        # Find the visual offset of the cursor in the document

        if not self.is_mounted:
            return

        self.app.clear_selection()

        cursor_location = selection.end

        self._track_cursor()
        if self._suppress_scroll:
            self._recompute_cursor_offset()
        else:
            scroll_x = self.scroll_offset.x
            self.scroll_cursor_visible()
            self._place_provisional(scroll_x)

        cursor_row, cursor_column = cursor_location

        try:
            character = self.document.column_slice(cursor_row, cursor_column, cursor_column + 1)
        except IndexError:
            character = ""

        # Record the location of a matching closing/opening bracket.
        match_location = self.find_matching_bracket(character, cursor_location)
        self._matching_bracket_location = match_location
        if match_location is not None:
            _, offset_y = self._cursor_offset
            self.refresh_lines(offset_y)

        self.app.cursor_position = self.cursor_screen_offset
        if previous_selection != selection:
            self.post_message(self.SelectionChanged(selection, self))

    def _watch_cursor_blink(self, blink: bool) -> None:
        if not self.is_mounted:
            return
        if blink and self.has_focus:
            self._restart_blink()
        else:
            self._pause_blink(visible=self.has_focus)

    def validate_read_only(self, read_only: bool) -> bool:
        """A lazy document cannot be edited: read-only stays on."""
        return read_only or self._lazy is not None

    def _watch_read_only(self, read_only: bool) -> None:
        self.set_class(read_only, "-read-only")
        self._set_theme(self._theme.name)

    def _recompute_cursor_offset(self):
        """Recompute the (x, y) coordinate of the cursor in the wrapped document (a provisional cursor row: from its window layout)."""
        self._cursor_offset = self.wrapped_document.location_to_offset(self.cursor_location)
        cursor = self._long_cursor
        layout = None if cursor is None or self.soft_wrap else cursor.provisional_layout(self.cursor_location[0])
        if layout is not None:
            self._cursor_offset = Offset(layout.left_x + layout.cursor_cells, self._cursor_offset.y)

    def find_matching_bracket(self, bracket: str, search_from: Location) -> Location | None:
        """If the character is a bracket, find the matching bracket.

        Args:
            bracket: The character we're searching for the matching bracket of.
            search_from: The location to start the search.

        Returns:
            The `Location` of the matching bracket, or `None` if it's not found.
            If the character is not available for bracket matching, `None` is returned.
            Always `None` for a lazily opened document.
        """
        if self._lazy is not None:
            return None  # a search would decode arbitrarily many rows
        match_location = None
        bracket_stack: list[str] = []
        if bracket in _OPENING_BRACKETS:
            # Search forwards for a closing bracket
            for candidate, candidate_location in self._yield_character_locations(search_from):
                if candidate in _OPENING_BRACKETS:
                    bracket_stack.append(candidate)
                elif candidate in _CLOSING_BRACKETS:
                    if bracket_stack and bracket_stack[-1] == _CLOSING_BRACKETS[candidate]:
                        bracket_stack.pop()
                        if not bracket_stack:
                            match_location = candidate_location
                            break
        elif bracket in _CLOSING_BRACKETS:
            # Search backwards for an opening bracket
            for (
                candidate,
                candidate_location,
            ) in self._yield_character_locations_reverse(search_from):
                if candidate in _CLOSING_BRACKETS:
                    bracket_stack.append(candidate)
                elif candidate in _OPENING_BRACKETS:
                    if bracket_stack and bracket_stack[-1] == _OPENING_BRACKETS[candidate]:
                        bracket_stack.pop()
                        if not bracket_stack:
                            match_location = candidate_location
                            break

        return match_location

    def _validate_selection(self, selection: Selection) -> Selection:
        """Clamp the selection to valid locations."""
        start, end = selection
        clamp_visitable = self.clamp_visitable
        return Selection(clamp_visitable(start), clamp_visitable(end))

    def _watch_language(self, language: str | None) -> None:
        """When the language is updated, update the type of document."""
        if self._lazy is not None:
            return  # a lazy document is highlighted only through `open(language=...)`
        self._set_document(self.document.text, language)

    def _watch_show_line_numbers(self) -> None:
        """The line number gutter contributes to virtual size, so recalculate."""
        self._rewrap_and_refresh_virtual_size()
        self.scroll_cursor_visible()

    def _watch_line_number_start(self) -> None:
        """The line number gutter max size might change and contributes to virtual size, so recalculate."""
        self._rewrap_and_refresh_virtual_size()
        self.scroll_cursor_visible()

    def _watch_indent_width(self) -> None:
        """Changing width of tabs will change the document display width."""
        self._rewrap_and_refresh_virtual_size()
        self.scroll_cursor_visible()

    def _watch_show_vertical_scrollbar(self) -> None:
        if self.wrap_width:
            self._rewrap_and_refresh_virtual_size()
        self.scroll_cursor_visible()

    def _watch_theme(self, theme: str) -> None:
        """We set the styles on this widget when the theme changes, to ensure that
        if padding is applied, the colors match.
        """
        self._set_theme(theme)

    def _app_theme_changed(self) -> None:
        self._set_theme(self._theme.name)

    def _set_theme(self, theme: str) -> None:
        theme_object: TextAreaTheme | None

        # If the user supplied a string theme name, find it and apply it.
        try:
            theme_object = self._themes[theme]
        except KeyError:
            theme_object = TextAreaTheme.get_builtin_theme(theme)
            if theme_object is None:
                raise ThemeDoesNotExist(
                    f"{theme!r} is not a builtin theme, or it has not been registered. "
                    f"To use a custom theme, register it first using `register_theme`, "
                    f"then switch to that theme by setting the `NovaTextArea.theme` attribute."
                ) from None

        self._theme = dataclasses.replace(theme_object)
        if theme_object:
            base_style = theme_object.base_style
            if base_style:
                color = base_style.color
                background = base_style.bgcolor
                if color:
                    self.styles.color = Color.from_rich_color(color)
                if background:
                    self.styles.background = Color.from_rich_color(background)
            else:
                # When the theme doesn't define a base style (e.g. the `css` theme),
                # the NovaTextArea background/color should fallback to its CSS colors.
                #
                # Since these styles may have already been changed by another theme,
                # we need to reset the background/color styles to the default values.
                self.styles.color = None
                self.styles.background = None

    @property
    def available_themes(self) -> set[str]:
        """A list of the names of the themes available to the `NovaTextArea`.

        The values in this list can be assigned `theme` reactive attribute of
        `NovaTextArea`.

        You can retrieve the full specification for a theme by passing one of
        the strings from this list into `TextAreaTheme.get_by_name(theme_name: str)`.

        Alternatively, you can directly retrieve a list of `TextAreaTheme` objects
        (which contain the full theme specification) by calling
        `TextAreaTheme.builtin_themes()`.
        """
        return {theme.name for theme in TextAreaTheme.builtin_themes()} | self._themes.keys()

    def register_theme(self, theme: TextAreaTheme) -> None:
        """Register a theme for use by the `NovaTextArea`.

        After registering a theme, you can set themes by assigning the theme
        name to the `NovaTextArea.theme` reactive attribute. For example
        `text_area.theme = "my_custom_theme"` where `"my_custom_theme"` is the
        name of the theme you registered.

        If you supply a theme with a name that already exists that theme
        will be overwritten.
        """
        self._themes[theme.name] = theme

    @property
    def available_languages(self) -> set[str]:
        """A set of the names of languages available to the `NovaTextArea`.

        The values in this set can be assigned to the `language` reactive attribute
        of `NovaTextArea`.

        The returned set contains the builtin languages installed with the syntax extras,
        plus those registered via the `register_language` method.
        """
        return set(BUILTIN_LANGUAGES) | self._languages.keys()

    def register_language(
        self,
        name: str,
        language: Language,
        highlight_query: str,
    ) -> None:
        """Register a language and corresponding highlight query.

        Calling this method does not change the language of the `NovaTextArea`.
        On switching to this language (via the `language` reactive attribute),
        syntax highlighting will be performed using the given highlight query.

        If a string `name` is supplied for a builtin supported language, then
        this method will update the default highlight query for that language.

        Registering a language only registers it to this instance of `NovaTextArea`.

        Args:
            name: The name of the language.
            language: A tree-sitter `Language` object.
            highlight_query: The highlight query to use for syntax highlighting this language.
        """
        self._languages[name] = TextAreaLanguage(name, language, highlight_query)

    def update_highlight_query(self, name: str, highlight_query: str) -> None:
        """Update the highlight query for an already registered language.

        Args:
            name: The name of the language.
            highlight_query: The highlight query to use for syntax highlighting this language.
        """
        if name not in self._languages:
            self._languages[name] = TextAreaLanguage(name, None, highlight_query)
        else:
            self._languages[name].highlight_query = highlight_query

        # If this is the currently loaded language, reload the document because
        # it could be a different highlight query for the same language.
        if name == self.language:
            self._set_document(self.text, name)

    def _resolve_language(self, language: str) -> tuple[Language, str]:
        """Return the tree-sitter language and highlight query for a language name.

        Args:
            language: The name of a user-registered or built-in language.

        Returns:
            The tree-sitter language and its highlight query.

        Raises:
            LanguageDoesNotExist: If neither a built-in nor a user-registered language has that name.
        """
        if language in self._languages:
            # User-registered languages take priority.
            highlight_query = self._languages[language].highlight_query
            document_language = self._languages[language].language
            if document_language is None:
                document_language = get_language(language)
        else:
            # No user-registered language, so attempt to use a built-in language.
            highlight_query = self._get_builtin_highlight_query(language)
            document_language = get_language(language)

        # No built-in language, and no user-registered language: use plain text and warn.
        if document_language is None:
            raise LanguageDoesNotExist(
                f"tree-sitter is available, but no built-in or user-registered language called {language!r}.\n"
                f"Ensure the language is installed (e.g. `pip install tree-sitter-ruby`)\n"
                f"Falling back to plain text."
            )
        return document_language, highlight_query

    def _enable_lazy_highlighting(self, language: str | None, highlight_limit: int) -> None:
        """Parse a small lazy document once for highlighting; above the limit no parser is created.

        Args:
            language: The requested language, or None for plain text.
            highlight_limit: Largest source (bytes) that is highlighted.
        """
        lazy = self._lazy
        if lazy is None or not language:
            return
        if lazy.length > highlight_limit:
            log.debug(f"Source of {lazy.length} bytes exceeds the highlight limit of {highlight_limit}; language {language!r} ignored.")
            return
        if not TREE_SITTER:
            log.warning("tree-sitter not available in this environment. Parsing disabled.")
            return
        document_language, highlight_query = self._resolve_language(language)
        try:
            syntax = SyntaxAwareDocument(lazy.read_all(highlight_limit), document_language)
        except SyntaxAwareDocumentError:
            log.warning(f"Parser not found for language {document_language!r}. Parsing disabled.")
            return
        lazy.attach_syntax(syntax)
        self._highlight_query = syntax.prepare_query(highlight_query)
        self._build_highlight_map()

    @property
    def highlight_active(self) -> bool:
        """True when syntax highlighting is in effect (a parser and highlight query exist)."""
        return self._highlight_query is not None

    def _set_document(self, text: str, language: str | None) -> None:
        """Construct and return an appropriate document.

        Args:
            text: The text of the document.
            language: The name of the language to use. This must correspond to a tree-sitter
                language available in the current environment (e.g. use `python` for `tree-sitter-python`).
                If None, the document will be treated as plain text.
        """
        self._highlight_query = None
        if self._lazy is not None:
            self.wrapped_document = LazyWrappedDocument(self._lazy, tab_width=self.indent_width)
            self.navigator = DocumentNavigator(self.wrapped_document)
            self._build_highlight_map()
            self._rewrap_and_refresh_virtual_size()
            return
        if TREE_SITTER and language:
            document_language, highlight_query = self._resolve_language(language)
            document: DocumentBase
            try:
                document = SyntaxAwareDocument(text, document_language)
            except SyntaxAwareDocumentError:
                document = Document(text)
                log.warning(f"Parser not found for language {document_language!r}. Parsing disabled.")
            else:
                self._highlight_query = document.prepare_query(highlight_query)
        elif language and not TREE_SITTER:
            # User has supplied a language i.e. `NovaTextArea(language="python")`, but they
            # don't have tree-sitter available in the environment. We fallback to plain text.
            log.warning(
                "tree-sitter not available in this environment. Parsing disabled.\n"
                "You may need to install the `syntax` extras alongside textual.\n"
                "Try `pip install 'textual[syntax]'` or '`poetry add textual[syntax]' to get started quickly.\n\n"
                "Alternatively, install tree-sitter manually (`pip install tree-sitter`) and then\n"
                "install the required language (e.g. `pip install tree-sitter-ruby`), then register it.\n"
                "and its highlight query using NovaTextArea.register_language().\n\n"
                "Falling back to plain text for now."
            )
            document = Document(text)
        else:
            # tree-sitter is available, but the user has supplied None or "" for the language.
            # Use a regular plain-text document.
            document = Document(text)

        self.document = document
        self.wrapped_document = WrappedDocument(document, tab_width=self.indent_width)
        self.navigator = DocumentNavigator(self.wrapped_document)
        self._build_highlight_map()
        self.move_cursor((0, 0))
        self._rewrap_and_refresh_virtual_size()

    @property
    def _visible_line_indices(self) -> tuple[int, int]:
        """Return the visible line indices as a tuple (top, bottom).

        Returns:
            A tuple (top, bottom) indicating the top and bottom visible line indices.
        """
        _, scroll_offset_y = self.scroll_offset
        return scroll_offset_y, scroll_offset_y + self.size.height

    def _watch_scroll_x(self) -> None:
        self.app.cursor_position = self.cursor_screen_offset

    def _watch_scroll_y(self) -> None:
        self.app.cursor_position = self.cursor_screen_offset

    def load_text(self, text: str) -> None:
        """Load text into the NovaTextArea.

        This will replace the text currently in the NovaTextArea and clear the edit history.

        Args:
            text: The text to load into the NovaTextArea.
        """
        if self._lazy is not None:
            msg = "a lazily opened document is read-only; load_text is not available"
            raise RuntimeError(msg)
        self.history.clear()
        self._set_document(text, self.language)
        self.post_message(self.Changed(self).set_sender(self))
        self.update_suggestion()

    def _on_resize(self) -> None:
        self._rewrap_and_refresh_virtual_size()

    def _watch_soft_wrap(self) -> None:
        self._map_cursor_for_wrap()
        self._rewrap_and_refresh_virtual_size()
        self.call_after_refresh(self._after_wrap_change)

    @property
    def wrap_width(self) -> int:
        """The width which gets used when the document wraps.

        Accounts for gutter, scrollbars, etc.
        """
        width, _ = self.scrollable_content_region.size
        cursor_width = 1
        if self.soft_wrap:
            return max(0, width - self.gutter_width - cursor_width)
        return 0

    def _rewrap_and_refresh_virtual_size(self) -> None:
        self.wrapped_document.wrap(self.wrap_width, tab_width=self.indent_width)
        self._line_cache.clear()
        self._refresh_size()

    @property
    def is_syntax_aware(self) -> bool:
        """True if the NovaTextArea is currently syntax aware - i.e. it's parsing document content."""
        return isinstance(self.document, SyntaxAwareDocument)

    def _yield_character_locations(self, start: Location) -> Iterable[tuple[str, Location]]:
        """Yields character locations starting from the given location.

        Does not yield location of line separator characters like `\\n`.

        Args:
            start: The location to start yielding from.

        Returns:
            Yields tuples of (character, (row, column)).
        """
        row, column = start
        document = self.document
        line_count = document.line_count

        while 0 <= row < line_count:
            line = document[row]
            while column < len(line):
                yield line[column], (row, column)
                column += 1
            column = 0
            row += 1

    def _yield_character_locations_reverse(self, start: Location) -> Iterable[tuple[str, Location]]:
        row, column = start
        document = self.document
        line_count = document.line_count

        while line_count > row >= 0:
            line = document[row]
            if column == -1:
                column = len(line) - 1
            while column >= 0:
                yield line[column], (row, column)
                column -= 1
            row -= 1

    def _refresh_size(self) -> None:
        """Update the virtual size of the NovaTextArea."""
        if self.soft_wrap:
            self.virtual_size = Size(0, self.wrapped_document.height)
        else:
            # +1 width to make space for the cursor resting at the end of the line
            width, height = self.document.get_size(self.indent_width)
            self.virtual_size = Size(width + self.gutter_width + 1, height)
        self._refresh_scrollbars()
        if self._lazy is not None:
            self._ensure_estimating()

    @property
    def _draw_cursor(self) -> bool:
        """Draw the cursor?"""
        if self.read_only:
            # If we are in read only mode, we don't want the cursor to blink
            return self.show_cursor and self.has_focus
        draw_cursor = (self.has_focus and not self.cursor_blink) or (self.cursor_blink and self._cursor_visible)
        return draw_cursor

    @property
    def _has_cursor(self) -> bool:
        """Is there a usable cursor?"""
        return not (self.read_only and not self.show_cursor)

    def get_line(self, line_index: int) -> Text:
        """Retrieve the line at the given line index.

        You can stylize the Text object returned here to apply additional
        styling to NovaTextArea content.

        Args:
            line_index: The index of the line.

        Returns:
            A `rich.Text` object containing the requested line.
        """
        lazy = self._lazy
        if lazy is not None and lazy.row_class(line_index) != "short":
            # Medium and long rows are never returned whole: a bounded prefix (the renderer uses windows).
            return Text(lazy.column_slice(line_index, 0, _PREFIX_CHARS), end="", no_wrap=True)
        line_string = self.document.get_line(line_index)
        return Text(line_string, end="", no_wrap=True)

    def render_lines(self, crop: Region) -> list[Strip]:
        theme = self._theme
        if theme:
            theme.apply_css(self)
        return super().render_lines(crop)

    def render_line(self, y: int) -> Strip:
        """Render a single line of the NovaTextArea. Called by Textual.

        Args:
            y: Y Coordinate of line relative to the widget region.

        Returns:
            A rendered line.
        """
        if self.placeholder and self._lazy is None and not self.text:
            placeholder_lines = Content.from_text(self.placeholder).wrap(self.content_size.width)
            if y < len(placeholder_lines):
                style = self.get_visual_style("text-area--placeholder")
                content = placeholder_lines[y].stylize(style)
                if self._draw_cursor and y == 0:
                    theme = self._theme
                    cursor_style = theme.cursor_style if theme else None
                    if cursor_style:
                        content = content.stylize(ContentStyle.from_rich_style(cursor_style), 0, 1)
                return Strip(content.render_segments(self.visual_style), content.cell_length)

        scroll_x, scroll_y = self.scroll_offset
        absolute_y = scroll_y + y
        selection = self.selection
        _, cursor_y = self._cursor_offset
        cache_key = (
            self.size,
            self.scrollable_content_region.width,  # a lazy row is windowed to it, and it lags `size` during the first layout
            scroll_x,
            absolute_y,
            (selection if selection.contains_line(absolute_y) or self.soft_wrap else selection.end[0] == absolute_y),
            (selection.end if (self._cursor_visible and self.cursor_blink and absolute_y == cursor_y) else None),
            self.theme,
            self._matching_bracket_location,
            self.match_cursor_bracket,
            self.soft_wrap,
            self.show_line_numbers,
            self.read_only,
            self.show_cursor,
            self.suggestion,
        )
        if (cached_line := self._line_cache.get(cache_key)) is not None:
            return cached_line
        line = self._render_line_guarded(y)
        self._line_cache[cache_key] = line
        return line

    def _blank_strip(self) -> Strip:
        theme = self._theme
        base_style = theme.base_style if theme and theme.base_style is not None else self.rich_style
        return Strip.blank(self.size.width, base_style)

    def _render_line_guarded(self, y: int) -> Strip:
        """Render a line; a lazy document renders blank rows when it is closed, failed, or its rows are not resolvable yet."""
        if self._lazy is None:
            return self._render_line(y)
        if self._lazy_closed or self._source_failed:
            return self._blank_strip()
        try:
            return self._render_line(y)
        except CoreSourceChanged as error:
            self._fail_source(str(error))
        except (RowUnavailable, IndexError):
            pass
        return self._blank_strip()

    def _render_window(self, lazy: LazyDocument, wrapped: LazyWrappedDocument, line_index: int, section_offset: int) -> Strip:
        """Render one medium or long row from a window: the visible columns plus a screen of margin (no wrap) or one section (wrap)."""
        theme = self._theme
        base_style = theme.base_style if theme and theme.base_style is not None else self.rich_style
        gutter_width = self.gutter_width
        visible = max(1, self.scrollable_content_region.size.width - gutter_width)
        tab_width = self.indent_width
        scroll_x, _ = self.scroll_offset
        window: WindowText | None
        if self.soft_wrap:
            window = section_window(lazy, wrapped, line_index, section_offset, tab_width)
            crop_start = window.phantom_cells
        else:
            cursor = self._long_cursor
            window = None if cursor is None else cursor.window(line_index, scroll_x, visible, self.selection.end[1])
            if window is None:
                window = window_text(lazy, line_index, scroll_x, visible, visible, tab_width)
            crop_start = window.phantom_cells + scroll_x - window.start_disp

        # The phantom cells keep the tab phase; every column is shifted by the window start.
        line = Text(" " * window.phantom_cells + window.text, end="", no_wrap=True)
        line.tab_size = tab_width
        if window.at_row_end:
            line.set_length(len(line) + 1)  # space at end for cursor
        shift = window.phantom_cells - window.start_column

        selection = self.selection
        start, end = selection
        cursor_row, cursor_column = end
        selection_top, selection_bottom = sorted(selection)
        selection_top_row, selection_top_column = selection_top
        selection_bottom_row, selection_bottom_column = selection_bottom

        has_cursor = self._has_cursor
        highlight_cursor_line = self.highlight_cursor_line and has_cursor
        cursor_line_style = theme.cursor_line_style if (theme and highlight_cursor_line) else None
        if has_cursor and cursor_line_style and cursor_row == line_index:
            line.stylize(cursor_line_style)

        selection_style = theme.selection_style if theme else None
        if start != end and selection_top_row <= line_index <= selection_bottom_row and selection_style:
            first = selection_top_column if line_index == selection_top_row else 0
            last = selection_bottom_column + shift if line_index == selection_bottom_row else len(line)
            line.stylize(selection_style, max(0, first + shift), max(0, last))

        if cursor_row == line_index and self._draw_cursor:
            cursor_style = theme.cursor_style if theme else None
            index = cursor_column + shift
            if cursor_style and index >= window.phantom_cells:
                line.stylize(cursor_style, index, index + 1)

        line.expand_tabs(tab_width)
        text_strip = Strip(line.render(self.app.console), cell_length=line.cell_len)
        text_strip = text_strip.crop(crop_start, crop_start + visible)
        missing = visible - text_strip.cell_length
        if (window.truncated or window.missing) and missing > 0:
            placeholder = Strip([Segment(_PLACEHOLDER_CELL * missing, Style(dim=True))], cell_length=missing)
            text_strip = Strip.join([text_strip, placeholder])
        line_style = cursor_line_style if (cursor_row == line_index and self.highlight_cursor_line) else (theme.base_style if theme else None)
        text_strip = text_strip.extend_cell_length(visible, line_style)

        if self.show_line_numbers:
            gutter_style = theme.cursor_line_gutter_style if (cursor_row == line_index and highlight_cursor_line) else theme.gutter_style
            gutter_content = str(line_index + self.line_number_start) if section_offset == 0 else ""
            gutter = Strip([Segment(f"{gutter_content:>{gutter_width - 2}}  ", gutter_style)], cell_length=gutter_width)
            text_strip = Strip.join([gutter, text_strip])
        self._ensure_estimating()
        return text_strip.apply_style(base_style)

    def _render_line(self, y: int) -> Strip:
        """Render a single line of the NovaTextArea. Called by Textual.

        Args:
            y: Y Coordinate of line relative to the widget region.

        Returns:
            A rendered line.
        """
        theme = self._theme
        base_style = theme.base_style if theme and theme.base_style is not None else self.rich_style

        wrapped_document = self.wrapped_document
        scroll_x, scroll_y = self.scroll_offset

        # Account for how much the NovaTextArea is scrolled.
        y_offset = y + scroll_y

        # If we're beyond the height of the document, render blank lines
        out_of_bounds = y_offset >= wrapped_document.height

        if out_of_bounds:
            return Strip.blank(self.size.width, base_style)

        # Get the line corresponding to this offset
        try:
            line_info = wrapped_document._offset_to_line_info[y_offset]
        except IndexError:
            line_info = None

        if line_info is None:
            return Strip.blank(self.size.width, base_style)

        line_index, section_offset = line_info

        lazy = self._lazy
        if lazy is not None and isinstance(wrapped_document, LazyWrappedDocument) and lazy.row_class(line_index) != "short":
            return self._render_window(lazy, wrapped_document, line_index, section_offset)

        line = self.get_line(line_index)
        line_character_count = len(line)
        line.tab_size = self.indent_width
        line.set_length(line_character_count + 1)  # space at end for cursor
        virtual_width, _virtual_height = self.virtual_size

        selection = self.selection
        start, end = selection
        cursor_row, cursor_column = end

        selection_top, selection_bottom = sorted(selection)
        selection_top_row, selection_top_column = selection_top
        selection_bottom_row, selection_bottom_column = selection_bottom

        highlight_cursor_line = self.highlight_cursor_line and self._has_cursor
        cursor_line_style = theme.cursor_line_style if (theme and highlight_cursor_line) else None
        has_cursor = self._has_cursor

        if has_cursor and cursor_line_style and cursor_row == line_index:
            line.stylize(cursor_line_style)

        # Selection styling
        if start != end and selection_top_row <= line_index <= selection_bottom_row:
            # If this row intersects with the selection range
            selection_style = theme.selection_style if theme else None
            cursor_row, _ = end
            if selection_style:
                if line_character_count == 0 and line_index != cursor_row:
                    # A simple highlight to show empty lines are included in the selection
                    line.plain = "▌"
                    line.stylize(Style(color=selection_style.bgcolor))
                else:
                    if line_index == selection_top_row == selection_bottom_row:
                        # Selection within a single line
                        line.stylize(
                            selection_style,
                            start=selection_top_column,
                            end=selection_bottom_column,
                        )
                    else:
                        # Selection spanning multiple lines
                        if line_index == selection_top_row:
                            line.stylize(
                                selection_style,
                                start=selection_top_column,
                                end=line_character_count,
                            )
                        elif line_index == selection_bottom_row:
                            line.stylize(selection_style, end=selection_bottom_column)
                        else:
                            line.stylize(selection_style, end=line_character_count)

        highlights = self._highlights
        if highlights and theme:
            line_bytes = _utf8_encode(line.plain)
            byte_to_codepoint = build_byte_to_codepoint_dict(line_bytes)
            get_highlight_from_theme = theme.syntax_styles.get
            line_highlights = highlights[line_index]
            for highlight_start, highlight_end, highlight_name in line_highlights:
                node_style = get_highlight_from_theme(highlight_name)
                if node_style is not None:
                    line.stylize(
                        node_style,
                        byte_to_codepoint.get(highlight_start, 0),
                        byte_to_codepoint.get(highlight_end) if highlight_end else None,
                    )

        # Highlight the cursor
        matching_bracket = self._matching_bracket_location
        match_cursor_bracket = self.match_cursor_bracket
        draw_matched_brackets = has_cursor and match_cursor_bracket and matching_bracket is not None and start == end

        if cursor_row == line_index:
            draw_cursor = self._draw_cursor
            if draw_matched_brackets:
                matching_bracket_style = theme.bracket_matching_style if theme else None
                if matching_bracket_style:
                    line.stylize(
                        matching_bracket_style,
                        cursor_column,
                        cursor_column + 1,
                    )

            if self.suggestion and (self.has_focus or not self.hide_suggestion_on_blur):
                suggestion_style = self.get_component_rich_style("text-area--suggestion")
                line = Text.assemble(
                    line[:cursor_column],
                    (self.suggestion, suggestion_style),
                    line[cursor_column:],
                )

            if draw_cursor:
                cursor_style = theme.cursor_style if theme else None
                if cursor_style:
                    line.stylize(cursor_style, cursor_column, cursor_column + 1)

        # Highlight the partner opening/closing bracket.
        if draw_matched_brackets:
            # mypy doesn't know matching bracket is guaranteed to be non-None
            assert matching_bracket is not None
            bracket_match_row, bracket_match_column = matching_bracket
            if theme and bracket_match_row == line_index:
                matching_bracket_style = theme.bracket_matching_style
                if matching_bracket_style:
                    line.stylize(
                        matching_bracket_style,
                        bracket_match_column,
                        bracket_match_column + 1,
                    )

        # Build the gutter text for this line
        gutter_width = self.gutter_width
        if self.show_line_numbers:
            if cursor_row == line_index and highlight_cursor_line:
                gutter_style = theme.cursor_line_gutter_style
            else:
                gutter_style = theme.gutter_style

            gutter_width_no_margin = gutter_width - 2
            gutter_content = str(line_index + self.line_number_start) if section_offset == 0 else ""
            gutter = [Segment(f"{gutter_content:>{gutter_width_no_margin}}  ", gutter_style)]
        else:
            gutter = []

        # TODO: Lets not apply the division each time through render_line.
        #  We should cache sections with the edit counts.
        wrap_offsets = wrapped_document.get_offsets(line_index)
        if wrap_offsets:
            sections = line.divide(wrap_offsets)  # TODO cache result with edit count
            line = sections[section_offset]
            line_tab_widths = wrapped_document.get_tab_widths(line_index)
            line.end = ""

            # Get the widths of the tabs corresponding only to the section of the
            # line that is currently being rendered. We don't care about tabs in
            # other sections of the same line.

            # Count the tabs before this section.
            tabs_before = 0
            for section_index in range(section_offset):
                tabs_before += sections[section_index].plain.count("\t")

            # Count the tabs in this section.
            tabs_within = line.plain.count("\t")
            section_tab_widths = line_tab_widths[tabs_before : tabs_before + tabs_within]
            line = expand_text_tabs_from_widths(line, section_tab_widths)
        else:
            line.expand_tabs(self.indent_width)

        base_width = self.scrollable_content_region.size.width if self.soft_wrap else max(virtual_width, self.region.size.width)
        target_width = base_width - self.gutter_width

        # Crop the line to show only the visible part (some may be scrolled out of view)
        console = self.app.console
        text_strip = Strip(line.render(console), cell_length=line.cell_len)
        if not self.soft_wrap:
            text_strip = text_strip.crop(scroll_x, scroll_x + virtual_width)

        # Stylize the line the cursor is currently on.
        if cursor_row == line_index and self.highlight_cursor_line:
            line_style = cursor_line_style
        else:
            line_style = theme.base_style if theme else None

        text_strip = text_strip.extend_cell_length(target_width, line_style)
        if gutter:
            strip = Strip.join([Strip(gutter, cell_length=gutter_width), text_strip])
        else:
            strip = text_strip

        return strip.apply_style(base_style)

    @property
    def text(self) -> str:
        """The entire text content of the document (always `""` for a lazily opened document)."""
        if self._lazy is not None:
            return ""
        return self.document.text

    @text.setter
    def text(self, value: str) -> None:
        """Replace the text currently in the NovaTextArea. This is an alias of `load_text`.

        Setting this value will clear the edit history.

        Args:
            value: The text to load into the NovaTextArea.
        """
        self.load_text(value)

    @property
    def selected_text(self) -> str:
        """The text between the start and end points of the current selection."""
        start, end = self.selection
        return self.get_text_range(start, end)

    @property
    def matching_bracket_location(self) -> Location | None:
        """The location of the matching bracket, if there is one."""
        return self._matching_bracket_location

    def get_text_range(self, start: Location, end: Location) -> str:
        """Get the text between a start and end location.

        Args:
            start: The start location.
            end: The end location.

        Returns:
            The text between start and end.
        """
        start, end = sorted((start, end))
        return self.document.get_text_range(start, end)

    def edit(self, edit: Edit) -> EditResult:
        """Perform an Edit.

        Args:
            edit: The Edit to perform.

        Returns:
            Data relating to the edit that may be useful. The data returned
            may be different depending on the edit performed.
        """
        if self.suggestion.startswith(edit.text):
            self.suggestion = self.suggestion[len(edit.text) :]
        else:
            self.suggestion = ""
        old_gutter_width = self.gutter_width
        result = edit.do(self)
        self.history.record(edit)
        new_gutter_width = self.gutter_width

        if old_gutter_width != new_gutter_width:
            self.wrapped_document.wrap(self.wrap_width, self.indent_width)
        else:
            self.wrapped_document.wrap_range(
                edit.top,
                edit.bottom,
                result.end_location,
            )

        edit.after(self)
        self._build_highlight_map()
        self.post_message(self.Changed(self))
        self.update_suggestion()
        self._refresh_size()
        return result

    def undo(self) -> None:
        """Undo the edits since the last checkpoint (the most recent batch of edits)."""
        if edits := self.history._pop_undo():
            self._undo_batch(edits)

    def action_undo(self) -> None:
        """Undo the edits since the last checkpoint (the most recent batch of edits)."""
        self.undo()

    def redo(self) -> None:
        """Redo the most recently undone batch of edits."""
        if edits := self.history._pop_redo():
            self._redo_batch(edits)

    def action_redo(self) -> None:
        """Redo the most recently undone batch of edits."""
        self.redo()

    def _undo_batch(self, edits: Sequence[Edit]) -> None:
        """Undo a batch of Edits.

        The sequence must be chronologically ordered by edit time.

        There must be no edits missing from the sequence, or the resulting content
        will be incorrect.

        Args:
            edits: The edits to undo, in the order they were originally performed.
        """
        if not edits:
            return

        old_gutter_width = self.gutter_width
        minimum_top = edits[-1].top
        maximum_old_bottom = (0, 0)
        maximum_new_bottom = (0, 0)
        for edit in reversed(edits):
            edit.undo(self)
            end_location = edit._edit_result.end_location if edit._edit_result else (0, 0)
            minimum_top = min(minimum_top, edit.top)
            maximum_old_bottom = max(maximum_old_bottom, end_location)
            maximum_new_bottom = max(maximum_new_bottom, edit.bottom)

        new_gutter_width = self.gutter_width
        if old_gutter_width != new_gutter_width:
            self.wrapped_document.wrap(self.wrap_width, self.indent_width)
        else:
            self.wrapped_document.wrap_range(minimum_top, maximum_old_bottom, maximum_new_bottom)

        self._refresh_size()
        for edit in reversed(edits):
            edit.after(self)
        self._build_highlight_map()
        self.post_message(self.Changed(self))
        self.update_suggestion()

    def _redo_batch(self, edits: Sequence[Edit]) -> None:
        """Redo a batch of Edits in order.

        The sequence must be chronologically ordered by edit time.

        Edits are applied from the start of the sequence to the end.

        There must be no edits missing from the sequence, or the resulting content
        will be incorrect.

        Args:
            edits: The edits to redo.
        """
        if not edits:
            return

        old_gutter_width = self.gutter_width
        minimum_top = edits[0].top
        maximum_old_bottom = (0, 0)
        maximum_new_bottom = (0, 0)
        for edit in edits:
            edit.do(self, record_selection=False)
            end_location = edit._edit_result.end_location if edit._edit_result else (0, 0)
            minimum_top = min(minimum_top, edit.top)
            maximum_new_bottom = max(maximum_new_bottom, end_location)
            maximum_old_bottom = max(maximum_old_bottom, edit.bottom)

        new_gutter_width = self.gutter_width
        if old_gutter_width != new_gutter_width:
            self.wrapped_document.wrap(self.wrap_width, self.indent_width)
        else:
            self.wrapped_document.wrap_range(
                minimum_top,
                maximum_old_bottom,
                maximum_new_bottom,
            )

        self._refresh_size()
        for edit in edits:
            edit.after(self)
        self._build_highlight_map()
        self.post_message(self.Changed(self))
        self.update_suggestion()

    async def on_event(self, event: events.Event) -> None:
        """Tell the scans that the user is interacting (they give way to the UI thread, see `Foreground`), then handle the event."""
        if isinstance(event, events.Key | events.MouseEvent):
            lazy = self._lazy
            if lazy is not None:
                lazy.foreground.touch()
        await super().on_event(event)

    async def _on_key(self, event: events.Key) -> None:
        """Handle key presses which correspond to document inserts."""
        self._restart_blink()

        if self.read_only:
            return

        key = event.key
        insert_values = {
            "enter": "\n",
        }
        if self.tab_behavior == "indent":
            if key == "escape":
                event.stop()
                event.prevent_default()
                self.screen.focus_next()
                return
            if self.indent_type == "tabs":
                insert_values["tab"] = "\t"
            else:
                insert_values["tab"] = " " * self._find_columns_to_next_tab_stop()

        if event.is_printable or key in insert_values:
            event.stop()
            event.prevent_default()
            insert = insert_values.get(key, event.character)
            # `insert` is not None because event.character cannot be
            # None because we've checked that it's printable.
            assert insert is not None
            start, end = self.selection
            self._replace_via_keyboard(insert, start, end)

    def _find_columns_to_next_tab_stop(self) -> int:
        """Get the location of the next tab stop after the cursors position on the current line.

        If the cursor is already at a tab stop, this returns the *next* tab stop location.

        Returns:
            The number of cells to the next tab stop from the current cursor column.
        """
        cursor_row, cursor_column = self.cursor_location
        line_text = self.document[cursor_row]
        indent_width = self.indent_width
        if not line_text:
            return indent_width

        width_before_cursor = self.get_column_width(cursor_row, cursor_column)
        spaces_to_insert = indent_width - ((indent_width + width_before_cursor) % indent_width)

        return spaces_to_insert

    def get_target_document_location(self, event: MouseEvent) -> Location:
        """Given a MouseEvent, return the row and column offset of the event in document-space.

        Args:
            event: The MouseEvent.

        Returns:
            The location of the mouse event within the document.
        """
        scroll_x, scroll_y = self.scroll_offset
        target_x = event.x - self.gutter_width + scroll_x - self.gutter.left
        target_y = event.y + scroll_y - self.gutter.top
        location = self.wrapped_document.offset_to_location(Offset(target_x, target_y))
        return location

    @property
    def gutter_width(self) -> int:
        """The width of the gutter (the left column containing line numbers).

        Returns:
            The cell-width of the line number column. If `show_line_numbers` is `False` returns 0.
        """
        # The longest number in the gutter plus two extra characters: `│ `.
        gutter_margin = 2
        gutter_width = len(str(self.document.line_count - 1 + self.line_number_start)) + gutter_margin if self.show_line_numbers else 0
        return gutter_width

    def _on_mount(self, event: events.Mount) -> None:
        def text_selection_started(screen: Screen) -> None:
            """Signal callback to unselect when arbitrary text selection starts."""
            self.selection = Selection(self.cursor_location, self.cursor_location)

        self.screen.text_selection_started_signal.subscribe(self, text_selection_started, immediate=True)

        # When `app.theme` reactive is changed, reset the theme to clear cached styles.
        self.watch(self.app, "theme", self._app_theme_changed, init=False)
        self.blink_timer = self.set_interval(
            0.5,
            self._toggle_cursor_blink_visible,
            pause=not (self.cursor_blink and self.has_focus),
        )
        lazy = self._lazy
        if lazy is not None and not self._lazy_closed:
            self._estimating = self._needs_estimates(lazy)
            self._estimate_timer = self.set_interval(_ESTIMATE_INTERVAL, self._estimate_tick, pause=not self._estimating)
            lazy.subscribe(self._index_callback)
            self._index_callback()  # the scan may have advanced (or ended) before the subscription: announce the current state once

    def _toggle_cursor_blink_visible(self) -> None:
        """Toggle visibility of the cursor for the purposes of 'cursor blink'."""
        if not self.screen.is_active:
            return

        self._cursor_visible = not self._cursor_visible
        _, cursor_y = self._cursor_offset
        self.refresh_lines(cursor_y)

    def _watch__cursor_visible(self) -> None:
        """When the cursor visibility is toggled, ensure the row is refreshed."""
        _, cursor_y = self._cursor_offset
        self.refresh_lines(cursor_y)

    def _restart_blink(self) -> None:
        """Reset the cursor blink timer."""
        if self.cursor_blink:
            self._cursor_visible = True
            if self.is_mounted:
                self.blink_timer.reset()

    def _pause_blink(self, visible: bool = True) -> None:
        """Pause the cursor blinking but ensure it stays visible."""
        self._cursor_visible = visible
        if self.is_mounted:
            self.blink_timer.pause()

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        """Update the cursor position, and begin a selection using the mouse."""
        target = self._mouse_target(event)
        if target is None:
            return
        self.selection = Selection.cursor(target)
        self._selecting = True
        # Capture the mouse so that if the cursor moves outside the
        # NovaTextArea widget while selecting, the widget still scrolls.
        self.capture_mouse()
        self._pause_blink(visible=False)
        self.history.checkpoint()

    async def _on_mouse_move(self, event: events.MouseMove) -> None:
        """Handles click and drag to expand and contract the selection."""
        if self._selecting:
            target = self._mouse_target(event)
            if target is None:
                return
            selection_start, _ = self.selection
            self.selection = Selection(selection_start, target)

    def _end_mouse_selection(self) -> None:
        """Finalize the selection that has been made using the mouse."""
        if self._selecting:
            self._selecting = False
            self.release_mouse()
            self.record_cursor_width()
            self._restart_blink()

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        """Finalize the selection that has been made using the mouse."""
        self._end_mouse_selection()

    def _on_hide(self, event: events.Hide) -> None:
        """Finalize the selection that has been made using the mouse when the widget is hidden."""
        self._end_mouse_selection()

    async def _on_paste(self, event: events.Paste) -> None:
        """When a paste occurs, insert the text from the paste event into the document."""
        if self.read_only:
            return
        if result := self._replace_via_keyboard(event.text, *self.selection):
            self.move_cursor(result.end_location)
            self.focus()

    def cell_width_to_column_index(self, cell_width: int, row_index: int) -> int:
        """Return the column that the cell width corresponds to on the given row.

        Args:
            cell_width: The cell width to convert.
            row_index: The index of the row to examine.

        Returns:
            The column corresponding to the cell width on that row.
        """
        column = self.document.column_at_display(row_index, cell_width, self.indent_width)
        # None means unknown (lazy documents only); the conservative answer is the start of the row.
        return 0 if column is None else column

    def clamp_visitable(self, location: Location) -> Location:
        """Clamp the given location to the nearest visitable location.

        Args:
            location: The location to clamp.

        Returns:
            The nearest location that we could conceivably navigate to using the cursor.
        """
        document = self.document

        row, column = location
        try:
            fits = column <= 0 or document.has_char_at(row, column - 1)
            length = None if fits else document.line_length(row)
        except IndexError:
            fits = False
            length = 0

        row = clamp(row, 0, document.line_count - 1)
        if fits or length is None:
            # An unknown length (lazy documents only) keeps the column instead of guessing a clamp.
            column = max(column, 0)
        else:
            column = clamp(column, 0, length)

        return row, column

    # --- Cursor/selection utilities
    @_guard_source(Offset(0, 0))
    def scroll_cursor_visible(self, center: bool = False, animate: bool = False) -> Offset:
        """Scroll the `NovaTextArea` such that the cursor is visible on screen.

        Args:
            center: True if the cursor should be scrolled to the center.
            animate: True if we should animate while scrolling.

        Returns:
            The offset that was scrolled to bring the cursor into view.
        """
        if not self._has_cursor:
            return Offset(0, 0)
        self._recompute_cursor_offset()

        x, y = self._cursor_offset
        scroll_offset = self.scroll_to_region(
            Region(x, y, width=3, height=1),
            spacing=Spacing(right=self.gutter_width),
            animate=animate,
            force=True,
            center=center,
        )
        return scroll_offset

    @_guard_source(None)
    def move_cursor(
        self,
        location: Location,
        select: bool = False,
        center: bool = False,
        record_width: bool = True,
    ) -> None:
        """Move the cursor to a location.

        Args:
            location: The location to move the cursor to.
            select: If True, select text between the old and new location.
            center: If True, scroll such that the cursor is centered.
            record_width: If True, record the cursor column cell width after navigating
                so that we jump back to the same width the next time we move to a row
                that is wide enough.
        """
        if not self._has_cursor:
            return
        if select:
            start, _end = self.selection
            self.selection = Selection(start, location)
        else:
            self.selection = Selection.cursor(location)

        if record_width:
            self.record_cursor_width()

        if center:
            self.scroll_cursor_visible(center)

        self.history.checkpoint()

    @_guard_source(None)
    def move_cursor_relative(
        self,
        rows: int = 0,
        columns: int = 0,
        select: bool = False,
        center: bool = False,
        record_width: bool = True,
    ) -> None:
        """Move the cursor relative to its current location in document-space.

        Args:
            rows: The number of rows to move down by (negative to move up)
            columns:  The number of columns to move right by (negative to move left)
            select: If True, select text between the old and new location.
            center: If True, scroll such that the cursor is centered.
            record_width: If True, record the cursor column cell width after navigating
                so that we jump back to the same width the next time we move to a row
                that is wide enough.
        """
        clamp_visitable = self.clamp_visitable
        _start, end = self.selection
        current_row, current_column = end
        target = clamp_visitable((current_row + rows, current_column + columns))
        self.move_cursor(target, select, center, record_width)

    @_guard_source(None)
    def select_line(self, index: int) -> None:
        """Select all the text in the specified line.

        Args:
            index: The index of the line to select (starting from 0).
        """
        try:
            length = self.document.line_length(index)
        except IndexError:
            return
        if length is None:
            # Unknown length (lazy documents only): leave the selection unchanged.
            return
        self.selection = Selection((index, 0), (index, length))
        self.record_cursor_width()

    def action_select_line(self) -> None:
        """Select all the text on the current line."""
        cursor_row, _ = self.cursor_location
        self.select_line(cursor_row)

    @_guard_source(None)
    def select_all(self) -> None:
        """Select all of the text in the `NovaTextArea`."""
        last_line = self.document.line_count - 1
        length_of_last_line = self.document.line_length(last_line)
        if length_of_last_line is None:
            # Unknown length (lazy documents only): leave the selection unchanged.
            return
        selection_start = (0, 0)
        selection_end = (last_line, length_of_last_line)
        self.selection = Selection(selection_start, selection_end)
        self.record_cursor_width()

    def action_select_all(self) -> None:
        """Select all the text in the document."""
        self.select_all()

    @property
    def cursor_location(self) -> Location:
        """The current location of the cursor in the document.

        This is a utility for accessing the `end` of `NovaTextArea.selection`.
        """
        return self.selection.end

    @cursor_location.setter
    def cursor_location(self, location: Location) -> None:
        """Set the cursor_location to a new location.

        If a selection is in progress, the anchor point will remain.
        """
        self.move_cursor(location, select=not self.selection.is_empty)

    @property
    def cursor_screen_offset(self) -> Offset:
        """The offset of the cursor relative to the screen."""
        cursor_x, cursor_y = self._cursor_offset
        scroll_x, scroll_y = self.scroll_offset
        region_x, region_y, _width, _height = self.content_region

        offset_x = region_x + cursor_x - scroll_x + self.gutter_width
        offset_y = region_y + cursor_y - scroll_y

        return Offset(offset_x, offset_y)

    @property
    def cursor_at_first_line(self) -> bool:
        """True if and only if the cursor is on the first line."""
        return self.selection.end[0] == 0

    @property
    def cursor_at_last_line(self) -> bool:
        """True if and only if the cursor is on the last line."""
        return self.selection.end[0] == self.document.line_count - 1

    @property
    def cursor_at_start_of_line(self) -> bool:
        """True if and only if the cursor is at column 0."""
        return self.selection.end[1] == 0

    @property
    def cursor_at_end_of_line(self) -> bool:
        """True if and only if the cursor is at the end of a row."""
        cursor_row, cursor_column = self.selection.end
        return not self.document.has_char_at(cursor_row, cursor_column)

    @property
    def cursor_at_start_of_text(self) -> bool:
        """True if and only if the cursor is at location (0, 0)"""
        return self.selection.end == (0, 0)

    @property
    def cursor_at_end_of_text(self) -> bool:
        """True if and only if the cursor is at the very end of the document."""
        return self.cursor_at_last_line and self.cursor_at_end_of_line

    # ------ Cursor movement actions
    @_guard_source(None)
    def action_cursor_left(self, select: bool = False) -> None:
        """Move the cursor one location to the left.

        If the cursor is at the left edge of the document, try to move it to
        the end of the previous line.

        If text is selected, move the cursor to the start of the selection.

        Args:
            select: If True, select the text while moving.
        """
        if not self._has_cursor:
            self.scroll_left()
            return
        if self._lazy_move(Op.LEFT, select=select):
            return
        target = self.get_cursor_left_location() if select or self.selection.is_empty else min(*self.selection)
        self.move_cursor(target, select=select)

    def get_cursor_left_location(self) -> Location:
        """Get the location the cursor will move to if it moves left.

        Returns:
            The location of the cursor if it moves left.
        """
        return self.navigator.get_location_left(self.cursor_location)

    @_guard_source(None)
    def action_cursor_right(self, select: bool = False) -> None:
        """Move the cursor one location to the right.

        If the cursor is at the end of a line, attempt to go to the start of the next line.

        If text is selected, move the cursor to the end of the selection.

        Args:
            select: If True, select the text while moving.
        """
        if not self._has_cursor:
            self.scroll_right()
            return
        if self.suggestion:
            self.insert(self.suggestion)
            return
        if self._lazy_move(Op.RIGHT, select=select):
            return
        target = self.get_cursor_right_location() if select or self.selection.is_empty else max(*self.selection)
        self.move_cursor(target, select=select)

    def get_cursor_right_location(self) -> Location:
        """Get the location the cursor will move to if it moves right.

        Returns:
            the location the cursor will move to if it moves right.
        """
        return self.navigator.get_location_right(self.cursor_location)

    @_guard_source(None)
    def action_cursor_down(self, select: bool = False) -> None:
        """Move the cursor down one cell.

        Args:
            select: If True, select the text while moving.
        """
        if not self._has_cursor:
            self.scroll_down()
            return
        if self._lazy_move(Op.DOWN, select=select):
            return
        target = self.get_cursor_down_location()
        self.move_cursor(target, record_width=False, select=select)

    def get_cursor_down_location(self) -> Location:
        """Get the location the cursor will move to if it moves down.

        Returns:
            The location the cursor will move to if it moves down.
        """
        return self.navigator.get_location_below(self.cursor_location)

    @_guard_source(None)
    def action_cursor_up(self, select: bool = False) -> None:
        """Move the cursor up one cell.

        Args:
            select: If True, select the text while moving.
        """
        if not self._has_cursor:
            self.scroll_up()
            return
        if self._lazy_move(Op.UP, select=select):
            return
        target = self.get_cursor_up_location()
        self.move_cursor(target, record_width=False, select=select)

    def get_cursor_up_location(self) -> Location:
        """Get the location the cursor will move to if it moves up.

        Returns:
            The location the cursor will move to if it moves up.
        """
        return self.navigator.get_location_above(self.cursor_location)

    @_guard_source(None)
    def action_cursor_line_end(self, select: bool = False) -> None:
        """Move the cursor to the end of the line."""
        if not self._has_cursor:
            self.scroll_end()
            return
        if self._lazy_move(Op.END, select=select):
            return
        location = self.get_cursor_line_end_location()
        self.move_cursor(location, select=select)

    def get_cursor_line_end_location(self) -> Location:
        """Get the location of the end of the current line.

        Returns:
            The (row, column) location of the end of the cursors current line.
        """
        return self.navigator.get_location_end(self.cursor_location)

    @_guard_source(None)
    def action_cursor_line_start(self, select: bool = False) -> None:
        """Move the cursor to the start of the line."""
        if not self._has_cursor:
            self.scroll_home()
            return
        if self._lazy_move(Op.HOME, select=select):
            return
        target = self.get_cursor_line_start_location(smart_home=True)
        self.move_cursor(target, select=select)

    def get_cursor_line_start_location(self, smart_home: bool = False) -> Location:
        """Get the location of the start of the current line.

        Args:
            smart_home: If True, use "smart home key" behavior - go to the first
                non-whitespace character on the line, and if already there, go to
                offset 0. Smart home only works when wrapping is disabled.

        Returns:
            The (row, column) location of the start of the cursors current line.
        """
        return self.navigator.get_location_home(self.cursor_location, smart_home=smart_home)

    @_guard_source(None)
    def action_cursor_word_left(self, select: bool = False) -> None:
        """Move the cursor left by a single word, skipping trailing whitespace.

        Args:
            select: Whether to select while moving the cursor.
        """
        if not self.show_cursor:
            return
        if self._lazy_move(Op.WORD_LEFT, select=select):
            return
        if self.cursor_at_start_of_text:
            return
        target = self.get_cursor_word_left_location()
        self.move_cursor(target, select=select)

    def get_cursor_word_left_location(self) -> Location:
        """Get the location the cursor will jump to if it goes 1 word left.

        At most `_WORD_WINDOW` (8192) characters left of the cursor are searched; a word that is longer
        ends at the window edge. Rows shorter than the window give the stock result.

        Returns:
            The location the cursor will jump on "jump word left".
        """
        cursor_row, cursor_column = self.cursor_location
        if cursor_row > 0 and cursor_column == 0:
            # Going to the previous row
            previous_length = self.document.line_length(cursor_row - 1)
            # Unknown length (lazy documents only): fall back to the start of the previous row.
            return cursor_row - 1, 0 if previous_length is None else previous_length

        # Staying on the same row
        window_start = max(0, cursor_column - _WORD_WINDOW)
        line = self.document.column_slice(cursor_row, window_start, cursor_column)
        search_string = line.rstrip()
        matches = list(re.finditer(self._word_pattern, search_string))
        cursor_column = window_start + matches[-1].start() if matches else window_start
        return cursor_row, cursor_column

    @_guard_source(None)
    def action_cursor_word_right(self, select: bool = False) -> None:
        """Move the cursor right by a single word, skipping leading whitespace."""
        if not self.show_cursor:
            return
        if self._lazy_move(Op.WORD_RIGHT, select=select):
            return
        if self.cursor_at_end_of_text:
            return

        target = self.get_cursor_word_right_location()
        self.move_cursor(target, select=select)

    def get_cursor_word_right_location(self) -> Location:
        """Get the location the cursor will jump to if it goes 1 word right.

        At most `_WORD_WINDOW` (8192) characters right of the cursor are searched; a word that is longer
        ends at the window edge. Rows shorter than the window give the stock result.

        Returns:
            The location the cursor will jump on "jump word right".
        """
        cursor_row, cursor_column = self.selection.end
        if cursor_row < self.document.line_count - 1 and not self.document.has_char_at(cursor_row, cursor_column):
            # Moving to the line below
            return cursor_row + 1, 0

        # Staying on the same line
        search_string = self.document.column_slice(cursor_row, cursor_column, cursor_column + _WORD_WINDOW)
        window_length = len(search_string)
        pre_strip_length = len(search_string)
        search_string = search_string.lstrip()
        strip_offset = pre_strip_length - len(search_string)

        matches = list(re.finditer(self._word_pattern, search_string))
        if matches:
            cursor_column += matches[0].start() + strip_offset
        else:
            cursor_column += window_length

        return cursor_row, cursor_column

    @_guard_source(None)
    def action_cursor_page_up(self) -> None:
        """Move the cursor and scroll up one page."""
        if not self.show_cursor:
            self.scroll_page_up()
            return
        if self._lazy_move(Op.PAGE_UP):
            return
        height = self.content_size.height
        _, cursor_location = self.selection
        target = self.navigator.get_location_at_y_offset(
            cursor_location,
            -height,
        )
        self.scroll_relative(y=-height, animate=False)
        self.move_cursor(target)

    @_guard_source(None)
    def action_cursor_page_down(self) -> None:
        """Move the cursor and scroll down one page."""
        if not self.show_cursor:
            self.scroll_page_down()
            return
        if self._lazy_move(Op.PAGE_DOWN):
            return
        height = self.content_size.height
        _, cursor_location = self.selection
        target = self.navigator.get_location_at_y_offset(
            cursor_location,
            height,
        )
        self.scroll_relative(y=height, animate=False)
        self.move_cursor(target)

    def get_column_width(self, row: int, column: int) -> int:
        """Get the cell offset of the column from the start of the row.

        Args:
            row: The row index.
            column: The column index (codepoint offset from start of row).

        Returns:
            The cell width of the column relative to the start of the row.
        """
        width = self.document.display_column(row, column, self.indent_width)
        # None means unknown (lazy documents only); the conservative answer is the start of the row.
        return 0 if width is None else width

    def record_cursor_width(self) -> None:
        """Record the current cell width of the cursor.

        This is used where we navigate up and down through rows.
        If we're in the middle of a row, and go down to a row with no
        content, then we go down to another row, we want our cursor to
        jump back to the same offset that we were originally at.
        """
        cursor_x_offset, _ = self.wrapped_document.location_to_offset(self.cursor_location)
        self.navigator.last_x_offset = cursor_x_offset

    # --- Editor operations
    def insert(
        self,
        text: str,
        location: Location | None = None,
        *,
        maintain_selection_offset: bool = True,
    ) -> EditResult:
        """Insert text into the document.

        Args:
            text: The text to insert.
            location: The location to insert text, or None to use the cursor location.
            maintain_selection_offset: If True, the active Selection will be updated
                such that the same text is selected before and after the selection,
                if possible. Otherwise, the cursor will jump to the end point of the
                edit.

        Returns:
            An `EditResult` containing information about the edit.
        """
        if len(text) > 1:
            self._restart_blink()
        if location is None:
            location = self.cursor_location
        return self.edit(Edit(text, location, location, maintain_selection_offset))

    def delete(
        self,
        start: Location,
        end: Location,
        *,
        maintain_selection_offset: bool = True,
    ) -> EditResult:
        """Delete the text between two locations in the document.

        Args:
            start: The start location.
            end: The end location.
            maintain_selection_offset: If True, the active Selection will be updated
                such that the same text is selected before and after the selection,
                if possible. Otherwise, the cursor will jump to the end point of the
                edit.

        Returns:
            An `EditResult` containing information about the edit.
        """
        return self.edit(Edit("", start, end, maintain_selection_offset))

    def replace(
        self,
        insert: str,
        start: Location,
        end: Location,
        *,
        maintain_selection_offset: bool = True,
    ) -> EditResult:
        """Replace text in the document with new text.

        Args:
            insert: The text to insert.
            start: The start location
            end: The end location.
            maintain_selection_offset: If True, the active Selection will be updated
                such that the same text is selected before and after the selection,
                if possible. Otherwise, the cursor will jump to the end point of the
                edit.

        Returns:
            An `EditResult` containing information about the edit.
        """
        return self.edit(Edit(insert, start, end, maintain_selection_offset))

    def clear(self) -> EditResult:
        """Delete all text from the document.

        Returns:
            An EditResult relating to the deletion of all content.
        """
        return self.delete((0, 0), self.document.end, maintain_selection_offset=False)

    def _delete_via_keyboard(
        self,
        start: Location,
        end: Location,
    ) -> EditResult | None:
        """Handle a deletion performed using a keyboard (as opposed to the API).

        Args:
            start: The start location of the text to delete.
            end: The end location of the text to delete.

        Returns:
            An EditResult or None if no edit was performed (e.g. on read-only mode).
        """
        if self.read_only:
            return None
        return self.delete(start, end, maintain_selection_offset=False)

    def _replace_via_keyboard(
        self,
        insert: str,
        start: Location,
        end: Location,
    ) -> EditResult | None:
        """Handle a replacement performed using a keyboard (as opposed to the API).

        Args:
            insert: The text to insert into the document.
            start: The start location of the text to replace.
            end: The end location of the text to replace.

        Returns:
            An EditResult or None if no edit was performed (e.g. on read-only mode).
        """
        if self.read_only:
            return None
        return self.replace(insert, start, end, maintain_selection_offset=False)

    def action_delete_left(self) -> None:
        """Deletes the character to the left of the cursor and updates the cursor location.

        If there's a selection, then the selected range is deleted.
        """
        if self.read_only:
            return

        selection = self.selection
        start, end = selection

        if selection.is_empty:
            end = self.get_cursor_left_location()

        self._delete_via_keyboard(start, end)

    def action_delete_right(self) -> None:
        """Deletes the character to the right of the cursor and keeps the cursor at the same location.

        If there's a selection, then the selected range is deleted.
        """
        if self.read_only:
            return

        selection = self.selection
        start, end = selection

        if selection.is_empty:
            end = self.get_cursor_right_location()

        self._delete_via_keyboard(start, end)

    def action_delete_line(self) -> None:
        """Deletes the lines which intersect with the selection."""
        if self.read_only:
            return
        self._delete_cursor_line()

    def _delete_cursor_line(self) -> EditResult | None:
        """Deletes the line (including the line terminator) that the cursor is on."""
        start, end = self.selection
        start, end = sorted((start, end))
        start_row, _start_column = start
        end_row, end_column = end

        # Generally editors will only delete line the end line of the
        # selection if the cursor is not at column 0 of that line.
        if start_row != end_row and end_column == 0 and end_row >= 0:
            end_row -= 1

        from_location = (start_row, 0)
        to_location = (end_row + 1, 0)

        deletion = self._delete_via_keyboard(from_location, to_location)
        if deletion is not None:
            self.move_cursor_relative(columns=end_column, record_width=False)
        return deletion

    def action_cut(self) -> None:
        """Cut text (remove and copy to clipboard)."""
        if self.read_only:
            return
        start, end = self.selection
        if start == end:
            edit_result = self._delete_cursor_line()
        else:
            edit_result = self._delete_via_keyboard(start, end)

        if edit_result is not None:
            self.app.copy_to_clipboard(edit_result.replaced_text)

    def action_copy(self) -> None:
        """Copy selection to clipboard."""
        selected_text = self.selected_text
        if selected_text:
            self.app.copy_to_clipboard(selected_text)
        else:
            raise SkipAction()

    def action_paste(self) -> None:
        """Paste from local clipboard."""
        if self.read_only:
            return
        clipboard = self.app.clipboard
        if result := self._replace_via_keyboard(clipboard, *self.selection):
            self.move_cursor(result.end_location)

    def action_delete_to_start_of_line(self) -> None:
        """Deletes from the cursor location to the start of the line."""
        if self.read_only:
            return

        if self.cursor_at_start_of_line:
            selection = self.selection
            start, end = selection
            if selection.is_empty:
                end = self.get_cursor_left_location()
            self._delete_via_keyboard(start, end)
        else:
            from_location = self.selection.end
            to_location = self.get_cursor_line_start_location()
            self._delete_via_keyboard(from_location, to_location)

    def action_delete_to_end_of_line(self) -> None:
        """Deletes from the cursor location to the end of the line."""
        from_location = self.selection.end
        to_location = self.get_cursor_line_end_location()
        self._delete_via_keyboard(from_location, to_location)

    async def action_delete_to_end_of_line_or_delete_line(self) -> None:
        """Deletes from the cursor location to the end of the line, or deletes the line.

        The line will be deleted if the line is empty.
        """
        # Assume we're just going to delete to the end of the line.
        action = "delete_to_end_of_line"
        if self.get_cursor_line_start_location() == self.get_cursor_line_end_location():
            # The line is empty, so we'll simply remove the line itself.
            action = "delete_line"
        elif self.selection.start == self.selection.end == self.get_cursor_line_end_location():
            # We're at the end of the line, so the kill delete operation
            # should join the next line to this.
            action = "delete_right"
        await self.run_action(action)

    def action_delete_word_left(self) -> None:
        """Deletes the word to the left of the cursor and updates the cursor location."""
        if self.cursor_at_start_of_text:
            return

        # If there's a non-zero selection, then "delete word left" typically only
        # deletes the characters within the selection range, ignoring word boundaries.
        start, end = self.selection
        if start != end:
            self._delete_via_keyboard(start, end)
            return

        to_location = self.get_cursor_word_left_location()
        self._delete_via_keyboard(self.selection.end, to_location)

    def action_delete_word_right(self) -> None:
        """Deletes the word to the right of the cursor and keeps the cursor at the same location.

        Note that the location that we delete to using this action is not the same
        as the location we move to when we move the cursor one word to the right.
        This action does not skip leading whitespace, whereas cursor movement does.
        """
        if self.cursor_at_end_of_text:
            return

        start, end = self.selection
        if start != end:
            self._delete_via_keyboard(start, end)
            return

        cursor_row, cursor_column = end

        # Check the current line for a word boundary
        line = self.document.column_slice(cursor_row, cursor_column, cursor_column + _WORD_WINDOW)
        matches = list(re.finditer(r"\s*\w+", line))

        at_end = not self.document.has_char_at(cursor_row, cursor_column)
        if matches:
            to_location = (cursor_row, cursor_column + matches[0].end())
        elif cursor_row < self.document.line_count - 1 and at_end:
            to_location = (cursor_row + 1, 0)
        else:
            to_location = (cursor_row, cursor_column + len(line))

        self._delete_via_keyboard(end, to_location)


@lru_cache(maxsize=128)
def build_byte_to_codepoint_dict(data: bytes) -> dict[int, int]:
    """Build a mapping of utf-8 byte offsets to codepoint offsets for the given data.

    Args:
        data: utf-8 bytes.

    Returns:
        A `dict[int, int]` mapping byte indices to codepoint indices within `data`.
    """
    byte_to_codepoint: dict[int, int] = {}
    current_byte_offset = 0
    code_point_offset = 0

    while current_byte_offset < len(data):
        byte_to_codepoint[current_byte_offset] = code_point_offset
        first_byte = data[current_byte_offset]

        # Single-byte character
        if (first_byte & 0b10000000) == 0:
            current_byte_offset += 1
        # 2-byte character
        elif (first_byte & 0b11100000) == 0b11000000:
            current_byte_offset += 2
        # 3-byte character
        elif (first_byte & 0b11110000) == 0b11100000:
            current_byte_offset += 3
        # 4-byte character
        elif (first_byte & 0b11111000) == 0b11110000:
            current_byte_offset += 4
        else:
            raise ValueError(f"Invalid UTF-8 byte: {first_byte}")

        code_point_offset += 1

    # Mapping for the end of the string
    byte_to_codepoint[current_byte_offset] = code_point_offset
    return byte_to_codepoint
