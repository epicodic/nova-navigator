"""The editor screen: menu bar, editor, bars, status line and hint bar with the `editor.*` actions."""

from __future__ import annotations

import asyncio
import os
from functools import partial
from pathlib import Path
from typing import ClassVar, Literal

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Input, Static

from nova_widgets.action import Action
from nova_widgets.file_provider import FileProvider, InMemoryFileProvider
from nova_widgets.key_types import KeyChord, KeySequence
from nova_widgets.keybindings_config import KeybindingsConfig
from nova_widgets.keymap import HintBar, KeymapRegistry
from nova_widgets.menu import Menu, MenuBar

from .bars import ConfirmBar, GotoBar, PathBar, SaveBar, format_sizes, parse_goto
from .core.byte_source import ChangeKind
from .core.save import check_path
from .document._cursor_anchor import CursorState
from .document._lazy_config import LazyConfig
from .document_view import DocumentView
from .editor_actions import build_editor_actions
from .editor_menus import build_menu_bar
from .search_bar import SearchBar, SearchStatus
from .status_line import StatusLine, StatusState, byte_percent, row_is_known
from .timed_text_area import TimedNovaTextArea
from .widget import ExternalCheck, NovaTextArea

QUIT_POLL_SECONDS = 0.02
NEEDLE_SHOWN = 40
"""Most characters of a needle that the status line shows."""

_EDITING_ACTIONS = (
    "editor.undo",
    "editor.redo",
    "editor.cut",
    "editor.copy",
    "editor.paste",
    "editor.select_all",
)
"""Actions that the editor widget also binds to its own keys; the screen swallows the old key when the action moved."""


def _column_kind(state: CursorState) -> Literal["exact", "provisional", "pending"]:
    if state is CursorState.RESOLVED:
        return "exact"
    if state is CursorState.PROVISIONAL:
        return "provisional"
    return "pending"


class EditorScreen(Screen[None]):
    """The editor: a menu bar with File, Edit, Search and View, the editor widget, the bars, the status line and a hint bar.

    The screen holds the actions, the menus, the keymap and the bars; everything about the open document lives in its `DocumentView`.
    Wrap mode and line numbers are reactive properties of the widget, not stored here.
    A host must forward `Key` events to `press_key` from `App.on_event`, because the keymap registry has to see a key before Textual's priority bindings do.
    """

    class Closed(Message):
        """Posted when the editor should close (quit or close after the questions); the host decides what follows."""

    DEFAULT_CSS: ClassVar[str] = """
    EditorScreen {
        layout: vertical;
    }

    #path_label {
        width: auto;
        padding: 0 1;
    }

    #editor {
        width: 1fr;
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

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel_save", "Cancel save", show=False)]

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
        poll_seconds: float = 2.0,
        quit_wait_seconds: float = 2.0,
    ) -> None:
        """Create the editor screen.

        Args:
            path: The file to open, or `None` for an empty buffer.
            keybindings: User keybinding overrides; `None` for defaults.
            file_provider: FileProvider for the file dialog; defaults to InMemoryFileProvider.
            soft_wrap: Start with soft wrapping enabled.
            config: Thresholds of the lazy document; `None` for defaults.
            editor_class: The editor widget class (allows injection for testing).
            standalone: `True` for standalone app (Quit in the File menu), `False` for embedded (Close).
            poll_seconds: Interval of the check for a change of the file on disk.
            quit_wait_seconds: Longest wait for a cancelled save before the quit question.
        """
        super().__init__()
        self._keybindings = keybindings
        self.file_provider = file_provider or InMemoryFileProvider()
        self.standalone = standalone
        self._poll_seconds = poll_seconds
        self._quit_wait_seconds = quit_wait_seconds

        self.ACTIONS: list[Action] = build_editor_actions()
        """The 18 `editor.*` actions of this screen; the menus, the keymap registry and the handlers use these very objects."""
        self._by_id: dict[str, Action] = {a.id: a for a in self.ACTIONS if a.id is not None}
        self._swallowed: set[KeySequence] = set()
        self.hint_bar: HintBar = HintBar()
        self.keymap_registry: KeymapRegistry = KeymapRegistry(self.hint_bar)
        self._apply_keymap()
        self.menu_bar: MenuBar = build_menu_bar(self._by_id, standalone=standalone)
        self._path_label = Static("", id="path_label", markup=False)
        self._full_path_text = ""
        self.menu_bar.add_right_widget(self._path_label)

        self.document, self._load_error = DocumentView.open(
            path,
            editor_class=editor_class,
            soft_wrap=soft_wrap,
            show_line_numbers=True,
            config=config,
            timing_file=os.environ.get("NOVA_EDIT_TIMING_FILE"),
        )

    # Keymap

    def _apply_keymap(self) -> None:
        """Resolve the overrides and load them into the registry, the actions and the hint bar."""
        if self._keybindings is not None:
            bindings = self._keybindings.resolve(self.ACTIONS)
        else:
            bindings = {a.id: a.initial_shortcut for a in self.ACTIONS if a.id is not None and a.initial_shortcut is not None}
        self.keymap_registry.reload(bindings, self.ACTIONS)
        for action in self.ACTIONS:
            if action.id not in bindings:
                action.set_shortcut(None)  # `reload` puts the default of an unmapped action back; menu and hint bar must show no key
        self._swallowed = set()
        for action_id in _EDITING_ACTIONS:
            action = self._by_id[action_id]
            initial = action.initial_shortcut
            if initial is not None and action.shortcut != initial and initial not in bindings.values():
                self._swallowed.add(initial)
        self.keymap_registry.on_focus_changed(self.focused)

    def reload_keymap(self) -> None:
        """Apply the current state of the key configuration (the host calls this after the key file changed)."""
        if self._keybindings is not None:
            self._keybindings.reload()
        self._apply_keymap()
        for menu in self.menu_bar.actions:
            if isinstance(menu, Menu):
                menu.add()  # no items: only measures the item widths again

    async def press_key(self, key: str) -> bool:
        """Run the action bound to `key`.

        Hosts call this for every raw `Key` event from `App.on_event` and stop the event when it returns `True`.
        A key press also dismisses a failure shown by the save bar.
        A focused `Input` keeps its own keys and does not run the editing actions (Undo, Redo, Cut, Copy, Paste, Select All).
        The old default key of an editing action that was moved or unmapped is swallowed, because the widget binds those keys itself.

        Args:
            key: The key string from the Key event (e.g. "ctrl+s").

        Returns:
            True if the key was handled, False otherwise.
        """
        bars = self.query(SaveBar)
        if bars and bars.first().failed:
            bars.first().clear()
        if isinstance(self.focused, Input) and self._input_keeps(key):
            return False
        if await self.keymap_registry.handle_key(key, self.app):
            return True
        if self._swallowed and self.focused is self.document.editor:
            return KeySequence((KeyChord.parse(key),)) in self._swallowed
        return False

    def _input_keeps(self, key: str) -> bool:
        """Whether a focused `Input` handles `key` itself: its own bindings (Ctrl+A is home, Ctrl+W deletes a word) and the editing keys."""
        if any(key in binding.key.split(",") for binding in Input.BINDINGS if isinstance(binding, Binding)):
            return True
        chord = KeySequence((KeyChord.parse(key),))
        return any(self._by_id[action_id].shortcut == chord for action_id in _EDITING_ACTIONS)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """The hint bar follows the focus."""
        self.keymap_registry.on_focus_changed(self.focused)

    async def on_menu_triggered(self, event: Menu.Triggered) -> None:
        """A menu item runs the same action as its key: the editor widget first, then the screen."""
        name = event.action.action
        if name is None:
            return
        editor = self.document.editor
        if self.focused is None:
            editor.focus()
        if await self.app.run_action(name, editor):
            return
        await self.app.run_action(name, self)

    # Composition

    def compose(self) -> ComposeResult:
        """Compose the menu bar, the editor, the bars, the status line and the hint bar."""
        yield self.menu_bar
        yield self.document.editor
        yield GotoBar()
        yield PathBar()
        yield SaveBar()
        yield ConfirmBar()
        yield SearchBar()
        yield SearchStatus()
        yield StatusLine(self._status_state)
        yield self.hint_bar

    def on_mount(self) -> None:
        """Show the path, start the poll for external changes and mirror the widget state in the menu."""
        if self._load_error is not None:
            self.notify(self._load_error, severity="error")
        self._show_path()
        self.call_after_refresh(self._constrain_path_label)
        self.set_interval(self._poll_seconds, self.poll)
        self._sync_view_actions()
        editor = self.document.editor
        self.watch(editor, "pending_progress", self._on_editor_state, init=False)
        self.watch(editor, "soft_wrap", self._on_editor_state, init=False)
        self.watch(editor, "show_line_numbers", self._on_editor_state, init=False)

    def on_resize(self, event: events.Resize) -> None:
        """Adjust the path label when the terminal is resized."""
        self._constrain_path_label()

    def _show_path(self) -> None:
        """Show the path of the document in the menu bar and the sub title of the app."""
        file_path = self.document.file_path
        text = "" if file_path is None else str(file_path)
        self._full_path_text = text
        self.app.sub_title = text
        self.call_after_refresh(self._constrain_path_label)

    def _constrain_path_label(self) -> None:
        """Ensure the path label does not cover menu entries by constraining its width and text."""
        terminal_width = self.size.width
        if terminal_width <= 0:
            return

        # Get the right edge of the last menu bar item
        menu_items = list(self.query("MenuBarItem"))
        if not menu_items:
            # No menu items yet, can't constrain
            return

        menu_right_edge = max(item.region.right for item in menu_items)

        # Path label CSS has padding: 0 1 (1 cell on left and right)
        label_padding = 2  # left padding (1) + right padding (1)

        # Calculate available width for path label (ensuring no overlap with menus)
        available_width = terminal_width - menu_right_edge - label_padding
        min_label_width = 2  # at least space for ellipsis or partial content

        if available_width < min_label_width:
            # Not enough space, hide the label
            self._path_label.styles.width = 0
            self._path_label.update("")
            return

        # Set max-width for the path label
        self._path_label.styles.width = available_width

        # Truncate text to fit in available width
        if len(self._full_path_text) > available_width:
            # Keep the end of the path with ellipsis
            truncated = "…" + self._full_path_text[-(available_width - 1) :]
            self._path_label.update(truncated)
        else:
            # Text fits, no need to truncate
            self._path_label.update(self._full_path_text)

    def _on_editor_state(self, _value: object) -> None:
        self._request_status()
        self._sync_view_actions()

    def _request_status(self) -> None:
        for status in self.query(StatusLine):
            status.request()

    def _status_state(self) -> StatusState | None:
        editor = self.document.editor
        row, column = editor.cursor_location
        cursor_state, _ = editor.peek_cursor_state()
        snapshot = editor.document.snapshot()
        progress = editor.pending_progress
        byte_offset = editor.cursor_byte_offset
        file_path = self.document.file_path
        return StatusState(
            file_name=None if file_path is None else file_path.name,
            line=row + 1 if row_is_known(row, snapshot.count, snapshot.complete) else None,
            column=column + 1,
            column_kind=_column_kind(cursor_state),
            byte_offset=byte_offset,
            byte_percent=byte_percent(byte_offset, editor.document.length),
            line_count=snapshot.count,
            line_count_exact=snapshot.complete,
            indexing_percent=100 if snapshot.complete else min(99, int(editor.indexing_progress * 100)),
            line_ending=editor.line_ending,
            modified=editor.modified,
            new_file=self.document.load_state == "new",
            wrap=editor.soft_wrap,
            goto_percent=None if progress is None else int(progress * 100),
        )

    # View actions: the widget owns the state, the checked items mirror it

    def _sync_view_actions(self) -> None:
        editor = self.document.editor
        self._by_id["editor.wrap_mode"].set_checked(editor.soft_wrap)
        self._by_id["editor.line_numbers"].set_checked(editor.show_line_numbers)

    def action_toggle_wrap(self) -> None:
        """Wrap Mode: flip `soft_wrap` of the widget."""
        self.document.editor.toggle_wrap()
        self._sync_view_actions()

    def action_toggle_line_numbers(self) -> None:
        """Line Numbers: flip `show_line_numbers` of the widget."""
        editor = self.document.editor
        editor.show_line_numbers = not editor.show_line_numbers
        self._sync_view_actions()

    def action_open_file(self) -> None:
        """Open: no file chooser exists yet, so say so."""
        self.notify("Open is not available yet", timeout=3.0)

    # Status refresh

    def on_nova_text_area_selection_changed(self, message: NovaTextArea.SelectionChanged) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_changed(self, message: NovaTextArea.Changed) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_index_progress(self, message: NovaTextArea.IndexProgress) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_indexing_complete(self, message: NovaTextArea.IndexingComplete) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_jump_completed(self, message: NovaTextArea.JumpCompleted) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_jump_rejected(self, message: NovaTextArea.JumpRejected) -> None:
        """Show why a goto was rejected; the cursor did not move."""
        self.notify(message.reason, severity="warning")

    # Bars

    @property
    def _save_bar(self) -> SaveBar:
        return self.query_one(SaveBar)

    @property
    def _confirm_bar(self) -> ConfirmBar:
        return self.query_one(ConfirmBar)

    @property
    def _path_bar(self) -> PathBar:
        return self.query_one(PathBar)

    @property
    def _search_bar(self) -> SearchBar:
        return self.query_one(SearchBar)

    @property
    def _search_status(self) -> SearchStatus:
        return self.query_one(SearchStatus)

    def _refocus_editor(self) -> None:
        self.document.editor.focus()

    # Polling

    def poll(self) -> None:
        """Timer or app focus: start one check of the file on a thread, unless one still runs or a save is under way."""
        document = self.document
        if document.polling:
            return
        check = document.editor.begin_external_check()
        if check is None:
            return
        document.polling = True
        self.run_worker(partial(self._poll_check, check), thread=True, group="poll", exit_on_error=False)

    def _poll_check(self, check: ExternalCheck) -> None:
        """Worker thread: only the `stat` calls of the check (they may block on a network file system); the UI thread applies the result."""
        kind = ChangeKind.UNCHANGED
        try:
            kind = check.run()
        finally:
            try:
                posted = self.post_message(events.Callback(partial(self._poll_done, check, kind)))
            except RuntimeError:  # the app is closing
                posted = False
            if not posted:
                self.document.polling = False

    def _poll_done(self, check: ExternalCheck, kind: ChangeKind) -> None:
        """UI thread: the poll ended; apply its result (the widget ignores one that a save made out of date)."""
        self.document.polling = False
        self.document.editor.apply_external_check(check, kind)

    # Save, Save As, Reload, Close, Quit

    def action_save(self) -> None:
        """Save the document; without a file the path bar asks for one."""
        editor = self.document.editor
        document = self.document
        if editor.saving:
            return
        if document.load_state == "failed":
            self.notify(
                "Not saved: the file could not be loaded, saving would replace it with an empty document. Use save as.",
                severity="error",
            )
            self._path_bar.open(document.file_path)
            return
        if editor.file_path is None:
            if document.file_path is None:
                self._path_bar.open(None)
            else:
                self._save_to(document.file_path)
            return
        if not editor.modified:
            self._save_bar.show_result("No changes to save")
            return
        editor.save()

    def action_save_as(self) -> None:
        """Open the save-as bar, prefilled with the current path."""
        editor = self.document.editor
        current = editor.file_path if editor.file_path is not None else self.document.file_path
        self._path_bar.open(current)

    def action_reload(self) -> None:
        """Reload the file; a modified document asks first."""
        editor = self.document.editor
        if editor.saving:
            self.notify("A save is running", severity="warning")
            return
        if editor.file_path is None:
            self.notify("Nothing to reload", severity="warning")
            return
        if editor.modified:
            self._confirm_bar.ask(None, editor.file_path, overwrite=False, save_as=False, reload=True)
            return
        editor.reload()

    def action_cancel_save(self) -> None:
        """Cancel a running save (Esc)."""
        self.document.editor.cancel_save()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Keep Esc for the widget and the bars unless a save runs."""
        if action == "cancel_save":
            return self.document.editor.saving
        return super().check_action(action, parameters)

    async def action_close_editor(self) -> None:
        """Close: the same flow as Quit."""
        await self._request_close()

    async def action_quit_editor(self) -> None:
        """Quit: cancel a search and a save, then ask when edits would be lost or a save still runs."""
        await self._request_close()

    async def _request_close(self) -> None:
        editor = self.document.editor
        if editor.searching:
            editor.cancel_search()
        editor.cancel_pending()
        if editor.saving:
            editor.cancel_save()
            for _ in range(round(self._quit_wait_seconds / QUIT_POLL_SECONDS)):
                if not editor.saving:
                    break
                await asyncio.sleep(QUIT_POLL_SECONDS)
            if editor.saving:
                self._confirm_bar.ask_quit(save_running=True)
                return
        if editor.modified:
            self._confirm_bar.ask_quit(save_running=False)
            return
        self.post_message(self.Closed())

    def _save_to(self, path: Path, *, confirmed: bool = False) -> None:
        """Save to `path` (a save as, or the first save of a new file)."""
        document = self.document
        path = path.expanduser().absolute()
        first_save = document.load_state == "new" and document.file_path is not None and os.path.realpath(path) == os.path.realpath(document.file_path)
        if first_save and not confirmed and check_path(path, None) is ChangeKind.CREATED:
            self._confirm_bar.ask(ChangeKind.CREATED, path, overwrite=True, save_as=True, reload=False)
            return
        document.editor.save(path, overwrite=confirmed or first_save)

    def on_confirm_bar_chosen(self, message: ConfirmBar.Chosen) -> None:
        """Carry out the answer of the confirm bar."""
        if message.choice == "quit":
            self.post_message(self.Closed())
            return
        self._refocus_editor()
        editor = self.document.editor
        if message.choice == "overwrite" and message.path is not None:
            if editor.file_path is None:
                self._save_to(message.path, confirmed=True)
            else:
                editor.save(message.path, overwrite=True)
        elif message.choice == "save_as":
            self._path_bar.open(message.path)
        elif message.choice == "reload" and not editor.saving:
            editor.reload()

    def on_nova_text_area_save_progress(self, message: NovaTextArea.SaveProgress) -> None:
        """Show the progress of the save."""
        self._save_bar.show_progress(message.phase, message.done, message.total)

    def on_nova_text_area_saved(self, message: NovaTextArea.Saved) -> None:
        """Show the result and follow the file the document is bound to now."""
        document = self.document
        document.load_state = "loaded"
        document.file_path = message.path
        self._show_path()
        self._request_status()
        self._save_bar.show_result(f"Saved  {message.path.name}  {format_sizes(message.length, message.length)}")
        document.deferred_change = None

    def on_nova_text_area_save_failed(self, message: NovaTextArea.SaveFailed) -> None:
        """Show the failure with its stage; it stays until a key is pressed."""
        reason = message.error.strerror or str(message.error)
        written = "  The file was written; press F5 to reload." if message.committed else ""
        self._save_bar.show_failure(f"Save failed ({message.stage}): {reason}{written}  Press a key")
        self._recheck_deferred_change(committed=message.committed)

    def on_nova_text_area_save_cancelled(self, message: NovaTextArea.SaveCancelled) -> None:
        """Show that the save was cancelled."""
        self._save_bar.show_result("Save cancelled")
        self._recheck_deferred_change()

    def on_nova_text_area_save_needs_confirmation(self, message: NovaTextArea.SaveNeedsConfirmation) -> None:
        """Ask before overwriting."""
        exists = message.kind in {ChangeKind.EXISTS, ChangeKind.CREATED}
        reload = not exists and self.document.editor.file_path is not None
        self._confirm_bar.ask(message.kind, message.path, overwrite=True, save_as=True, reload=reload)

    def on_nova_text_area_source_changed(self, message: NovaTextArea.SourceChanged) -> None:
        """Ask what to do about a file that changed on disk."""
        if self.document.editor.saving:  # asking now would compete with the save: remember it, the end of the save decides
            self.document.deferred_change = message.kind
            return
        self._announce_change(message.kind)

    def _announce_change(self, kind: ChangeKind) -> None:
        """Ask what to do about a file that changed on disk, unless the view is not stale any more.

        A `SourceChanged` posted during a save can be handled after the save ended; when the save rebased the document onto its own file the view
        is no longer stale and the check finds nothing, so there is nothing to ask.
        """
        editor = self.document.editor
        current = editor.check_external_change()  # the stale kind while the view is stale, else a fresh check
        if current is ChangeKind.UNCHANGED:
            return
        self._confirm_bar.ask(kind, editor.file_path, overwrite=editor.modified, save_as=True, reload=editor.file_path is not None)

    def _recheck_deferred_change(self, *, committed: bool = False) -> None:
        """After a save: announce the change that was seen while it ran unless the save rebased the document (the file is then our own)."""
        document = self.document
        deferred, document.deferred_change = document.deferred_change, None
        editor = document.editor
        if deferred is None or committed or editor.saving:
            return
        kind = editor.check_external_change()  # still stale, or changed again: either way the current kind
        if kind is not ChangeKind.UNCHANGED:
            self._announce_change(kind)

    def on_nova_text_area_reloaded(self, message: NovaTextArea.Reloaded) -> None:
        """Show that the file was reloaded."""
        self._request_status()
        self._save_bar.show_result("Reloaded")

    def on_nova_text_area_reload_failed(self, message: NovaTextArea.ReloadFailed) -> None:
        """Show why the file could not be reloaded."""
        self._save_bar.show_failure(f"Reload failed: {message.error.strerror or message.error}  Press a key")

    # Search and Go to

    def search(self, needle: str, *, backward: bool = False, case_sensitive: bool | None = None) -> None:
        """Search the editor for `needle` and remember it for Find Next and Find Previous.

        Args:
            needle: The text to find (it may hold line breaks, which a bar cannot take).
            backward: Search towards the start of the document.
            case_sensitive: Distinguish case; the case state of the search bar when `None`.
        """
        if not needle:
            return
        if case_sensitive is None:
            case_sensitive = self._search_bar.case_sensitive
        document = self.document
        document.needle = needle
        document.last_backward = backward
        document.editor.search(needle, backward=backward, case_sensitive=case_sensitive)

    def _repeat_search(self, *, backward: bool) -> None:
        needle = self.document.needle
        if needle is None:
            self.action_find()
            return
        self.search(needle, backward=backward)

    def action_find(self) -> None:
        """Open the search bar."""
        self._search_bar.open()

    def action_find_next(self) -> None:
        """Repeat the last search forward."""
        self._repeat_search(backward=False)

    def action_find_previous(self) -> None:
        """Repeat the last search backward."""
        self._repeat_search(backward=True)

    def action_goto(self) -> None:
        """Show or hide the goto bar."""
        goto_bar = self.query_one(GotoBar)
        goto_bar.display = not goto_bar.display
        if goto_bar.display:
            goto_bar.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission from the path bar, the search bar and the goto bar."""
        if event.input.id == "path_bar":
            text = event.value.strip()
            if not text:
                self.notify("No path given", severity="warning")
                return
            self._path_bar.action_close()
            self._save_to(Path(text))
            return
        if event.input.id == "search_bar":
            needle = event.value
            if not needle:
                return
            self._search_bar.action_close()
            self.search(needle, case_sensitive=self._search_bar.case_sensitive)
            return
        if event.input.id != "goto_bar":
            return

        target = parse_goto(event.value)
        if target is None:
            self.notify("Invalid goto format", severity="warning")
            return

        editor = self.document.editor
        if target.kind == "line":
            editor.goto_line(target.value)
        else:  # byte
            editor.goto_byte(target.value)

        goto_bar = self.query_one(GotoBar)
        goto_bar.display = False
        goto_bar.value = ""
        editor.focus()

    def on_nova_text_area_search_progress(self, message: NovaTextArea.SearchProgress) -> None:
        """Show the progress of the search."""
        if not self.document.editor.searching:
            return
        percent = 100 if message.total <= 0 else round(message.done * 100 / message.total)
        sizes = format_sizes(message.done, message.total).replace(" / ", " of ")
        self._search_status.show_progress(f"Searching {percent}% ({sizes}), Esc cancels")

    def on_nova_text_area_search_found(self, message: NovaTextArea.SearchFound) -> None:
        """Show the result of a search that found a match."""
        if not message.wrapped:
            text = "Found"
        else:
            text = "Wrapped to the bottom" if self.document.last_backward else "Wrapped to the top"
        self._search_status.show_result(text)

    def on_nova_text_area_search_not_found(self, message: NovaTextArea.SearchNotFound) -> None:
        """Show that nothing was found."""
        needle = message.needle
        if len(needle) > NEEDLE_SHOWN:
            needle = needle[:NEEDLE_SHOWN] + chr(0x2026)
        self._search_status.show_result(f"Not found: {needle}")

    def on_nova_text_area_search_cancelled(self, message: NovaTextArea.SearchCancelled) -> None:
        """Show why a search ended without a result; a replaced one is followed by its successor."""
        if message.reason == "replaced":
            return
        suffix = "" if message.reason == "cancelled" else f": {message.reason}"
        self._search_status.show_result(f"Search cancelled{suffix}")

    def on_nova_text_area_search_failed(self, message: NovaTextArea.SearchFailed) -> None:
        """Show why a search failed."""
        self._search_status.show_result(f"Search failed: {message.error}")
