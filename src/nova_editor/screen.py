"""The editor screen: composition, per-screen actions, menus, bars and document holder."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Static

from nova_widgets.action import Action
from nova_widgets.file_provider import FileProvider, InMemoryFileProvider
from nova_widgets.keybindings_config import KeybindingsConfig
from nova_widgets.keymap import HintBar, KeymapRegistry
from nova_widgets.menu import MenuBar

from .bars import ConfirmBar, GotoBar, PathBar, SaveBar
from .document._lazy_config import LazyConfig
from .document_view import DocumentView
from .editor_actions import build_editor_actions
from .editor_menus import build_menu_bar
from .search_bar import SearchBar
from .status_line import StatusLine, StatusState
from .timed_text_area import TimedNovaTextArea


class EditorScreen(Screen[None]):
    """The main editor screen with menus, bars, editor widget, and status line.

    This screen holds:
    - Per-screen ACTIONS (18 editor actions)
    - Per-screen MenuBar built from those actions
    - Per-screen KeymapRegistry and HintBar
    - A DocumentView (holds the editor widget and file metadata)
    - All the bars (GotoBar, PathBar, SaveBar, SearchBar, ConfirmBar)
    - StatusLine showing edit progress and file status

    The document is held by reference only; no per-document state is duplicated.
    Wrap mode and line numbers are reactive properties of the widget, not stored here.
    """

    DEFAULT_CSS: ClassVar[str] = """
    EditorScreen {
        layout: vertical;
    }

    #editor_header {
        height: 1;
        dock: top;
    }

    #editor_container {
        width: 1fr;
        height: 1fr;
    }

    #editor {
        width: 100%;
        height: 1fr;
    }

    #status_line {
        width: 100%;
        height: 1;
        background: $panel;
        color: $text;
    }

    #goto_bar {
        width: 100%;
        height: 1;
        border: solid $primary;
        display: none;
    }

    #path_bar {
        width: 100%;
        height: 1;
        border: none;
        display: none;
    }

    #search_bar {
        width: 100%;
        height: 1;
        border: none;
        display: none;
    }

    #save_bar, #confirm_bar, #search_status {
        width: 100%;
        height: 1;
        display: none;
    }

    #confirm_bar {
        background: $warning;
        color: $text;
    }
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        keybindings: KeybindingsConfig | None = None,
        file_provider: FileProvider | None = None,
        soft_wrap: bool = False,
        config: LazyConfig | None = None,
        editor_class: type[TimedNovaTextArea] = TimedNovaTextArea,
        standalone: bool = False,
    ) -> None:
        """Create the editor screen.

        Args:
            path: The file to open, or `None` for an empty buffer.
            keybindings: User keybinding overrides; `None` for defaults.
            file_provider: FileProvider for the file dialog; defaults to InMemoryFileProvider.
            soft_wrap: Start with soft wrapping enabled.
            config: Thresholds of the lazy document; `None` for defaults.
            editor_class: The editor widget class (allows injection for testing).
            standalone: `True` for standalone app, `False` for embedded.
        """
        super().__init__()
        self.path = path
        self._keybindings = keybindings
        self.file_provider = file_provider or InMemoryFileProvider()
        self._soft_wrap = soft_wrap
        self._config = config
        self._editor_class = editor_class
        self.standalone = standalone

        # Build per-screen actions (fresh on every screen instance)
        self.ACTIONS: list[Action] = build_editor_actions()

        # Apply keybindings overrides to actions if provided
        if keybindings:
            self._apply_keybindings_overrides()

        # Build per-screen menu bar
        actions_dict = {a.id: a for a in self.ACTIONS if a.id}
        self.menu_bar: MenuBar = build_menu_bar(actions_dict, standalone=standalone)

        # Create per-screen hint bar (must be created before keymap registry)
        self.hint_bar: HintBar = HintBar()

        # Build per-screen keymap registry (needs hint bar)
        self.keymap_registry: KeymapRegistry = KeymapRegistry(self.hint_bar)

        # Create the document
        self.document, self._load_error = DocumentView.open(
            path,
            editor_class=self._editor_class,
            soft_wrap=self._soft_wrap,
            config=self._config,
            timing_file=None,
        )

        # Set up status state
        self._status_state: StatusState | None = None

        # Show load error as notification
        if self._load_error:
            self.notify(self._load_error, severity="error", timeout=5.0)

    def _apply_keybindings_overrides(self) -> None:
        """Apply keybinding overrides from config to actions."""
        if self._keybindings is None:
            return
        for action in self.ACTIONS:
            if action.id in self._keybindings._overrides:
                action.set_shortcut(self._keybindings._overrides[action.id])

    def compose(self) -> ComposeResult:
        """Compose the screen with menu bar, bars, editor, status line and hint bar."""
        yield self.menu_bar
        yield Container(
            Vertical(
                self.document.editor,
                id="editor_container",
            ),
            GotoBar(),
            PathBar(),
            SearchBar(),
            SaveBar(),
            ConfirmBar(),
            Static(id="search_status"),
        )
        yield StatusLine(self._get_status_state)
        yield self.hint_bar

    def _get_status_state(self) -> StatusState | None:
        """Return the current status state based on the editor."""
        editor = self.document.editor
        # Placeholder: return minimal status state
        # Full implementation in later tasks
        return StatusState(
            line=1,
            column=1,
            column_kind="exact",
            byte_offset=0,
            line_count=editor.line_count,
            line_count_exact=editor.indexing_complete,
            indexing_percent=int(editor.indexing_progress * 100),
            line_ending="LF",
            modified=editor.modified,
            new_file=self.document.load_state == "new",
            wrap=editor.soft_wrap,
            goto_percent=None,
        )

    def on_mount(self) -> None:
        """Set up the screen after mounting."""
        # Connect checkable items to widget state
        self._setup_checkable_items()

        # Watch editor reactive properties and update action state
        self.watch(self.document.editor, "soft_wrap", self._on_soft_wrap_changed, init=False)
        self.watch(self.document.editor, "show_line_numbers", self._on_show_line_numbers_changed, init=False)

    def _setup_checkable_items(self) -> None:
        """Connect checkable action items to widget reactive properties."""
        # Get the wrap mode and line numbers actions
        wrap_action = next((a for a in self.ACTIONS if a.id == "editor.wrap_mode"), None)
        line_nums_action = next((a for a in self.ACTIONS if a.id == "editor.line_numbers"), None)

        if wrap_action:
            # Set initial checked state based on widget
            wrap_action.set_checked(self.document.editor.soft_wrap)

        if line_nums_action:
            # Set initial checked state based on widget
            line_nums_action.set_checked(self.document.editor.show_line_numbers)

    def _on_soft_wrap_changed(self, _value: object) -> None:
        """Update wrap_mode action state when soft_wrap changes."""
        wrap_action = next((a for a in self.ACTIONS if a.id == "editor.wrap_mode"), None)
        if wrap_action:
            wrap_action.set_checked(self.document.editor.soft_wrap)

    def _on_show_line_numbers_changed(self, _value: object) -> None:
        """Update line_numbers action state when show_line_numbers changes."""
        line_nums_action = next((a for a in self.ACTIONS if a.id == "editor.line_numbers"), None)
        if line_nums_action:
            line_nums_action.set_checked(self.document.editor.show_line_numbers)

    def action_open_file(self) -> None:
        """Show the neutral 'Open is not available yet' stub message."""
        self.notify("Open is not available yet", timeout=3.0)

    def action_save(self) -> None:
        """Save the current document."""
        # Placeholder: will be implemented in later tasks

    def action_save_as(self) -> None:
        """Save the document under another name."""
        # Placeholder: will be implemented in later tasks

    def action_reload(self) -> None:
        """Reload the file from disk."""
        # Placeholder: will be implemented in later tasks

    def action_close_editor(self) -> None:
        """Close the editor (or navigate back in embedded mode)."""
        if self.standalone:
            self.app.exit()
        else:
            self.app.pop_screen()

    def action_quit_editor(self) -> None:
        """Quit the editor (standalone only)."""
        self.app.exit()

    def action_undo(self) -> None:
        """Undo the last edit."""
        self.document.editor.action_undo()

    def action_redo(self) -> None:
        """Redo the last undone edit."""
        self.document.editor.action_redo()

    def action_cut(self) -> None:
        """Cut the selection."""
        self.document.editor.action_cut()

    def action_copy(self) -> None:
        """Copy the selection."""
        self.document.editor.action_copy()

    def action_paste(self) -> None:
        """Paste from the clipboard."""
        self.document.editor.action_paste()

    def action_select_all(self) -> None:
        """Select the whole document."""
        self.document.editor.action_select_all()

    def action_find(self) -> None:
        """Show the search bar."""
        search_bar = self.query_one("#search_bar")
        search_bar.display = True
        search_bar.focus()

    def action_find_next(self) -> None:
        """Repeat the search forward."""
        # Placeholder: will be implemented in later tasks

    def action_find_previous(self) -> None:
        """Repeat the search backward."""
        # Placeholder: will be implemented in later tasks

    def action_goto(self) -> None:
        """Show the goto bar."""
        goto_bar = self.query_one("#goto_bar")
        goto_bar.display = True
        goto_bar.focus()

    def action_toggle_line_numbers(self) -> None:
        """Toggle line numbers on/off."""
        self.document.editor.show_line_numbers = not self.document.editor.show_line_numbers

    def action_toggle_wrap(self) -> None:
        """Toggle soft wrap on/off."""
        self.document.editor.soft_wrap = not self.document.editor.soft_wrap
