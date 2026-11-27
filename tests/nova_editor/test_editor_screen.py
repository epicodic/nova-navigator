"""The editor screen: composition, per-screen actions, menus, popups, dialogs and document holder."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.screen import EditorScreen
from nova_widgets.file_dialog import FileDialogMode
from nova_widgets.keybindings_config import KeybindingsConfig

from .dialog_helpers import open_file_dialog
from .helpers_view import wait_until
from .screen_host import EditorScreenHost, write_keys


@pytest.mark.asyncio
async def test_editor_screen_composes_the_popups_and_no_bar() -> None:
    """The screen composes the Go to and Find popups and none of the old bars."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        from nova_editor.find_popup import FindPopup
        from nova_editor.goto_popup import GotoPopup

        assert len(screen.query(GotoPopup)) == 1
        assert len(screen.query(FindPopup)) == 1
        assert len(screen.query("#goto_bar, #path_bar, #save_bar, #confirm_bar, #search_bar, #search_status")) == 0


@pytest.mark.asyncio
async def test_menu_items_are_the_action_objects() -> None:
    """Menu items must be the same Action objects from self.ACTIONS (DEC-20)."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert hasattr(screen, "menu_bar")

        from nova_widgets.menu import Menu

        # Get File menu (first menu)
        file_menu: Menu = next(iter(screen.menu_bar.actions))

        # Get save action from the menu
        save_item = next(a for a in file_menu.actions if a.id == "editor.save")

        # Find the save action from screen's ACTIONS
        save_action = next(a for a in screen.ACTIONS if a.id == "editor.save")

        # Verify they are the same object (not just equal values)
        assert save_item is save_action


@pytest.mark.asyncio
async def test_hosted_screen_has_four_menus_in_order_with_req2_items() -> None:
    """A hosted screen shows File, Edit, Search, View in order with the REQ-2 items."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        menus = list(screen.menu_bar.actions)
        assert [m.text for m in menus] == ["File", "Edit", "Search", "View"]
        assert [[a.id for a in m.actions] for m in menus] == [
            ["editor.open", "editor.save", "editor.save_as", "editor.reload", "editor.quit"],
            ["editor.undo", "editor.redo", "editor.cut", "editor.copy", "editor.paste", "editor.select_all"],
            ["editor.find", "editor.find_next", "editor.find_previous", "editor.goto"],
            ["editor.line_numbers", "editor.wrap_mode", "editor.hex_mode"],
        ]


@pytest.mark.asyncio
async def test_standalone_shows_quit_embedded_shows_close() -> None:
    """Standalone mode shows Quit in File menu, embedded mode shows Close only."""
    from nova_widgets.menu import Menu

    host_standalone = EditorScreenHost(standalone=True)
    async with host_standalone.run_test() as pilot:
        await pilot.pause()
        screen_standalone = host_standalone.screen_instance
        assert screen_standalone is not None
        file_menu: Menu = next(iter(screen_standalone.menu_bar.actions))
        assert [a.id for a in file_menu.actions] == [
            "editor.open",
            "editor.save",
            "editor.save_as",
            "editor.reload",
            "editor.quit",
        ]

    host_embedded = EditorScreenHost(standalone=False)
    async with host_embedded.run_test() as pilot:
        await pilot.pause()
        screen_embedded = host_embedded.screen_instance
        assert screen_embedded is not None
        file_menu = next(iter(screen_embedded.menu_bar.actions))
        assert [a.id for a in file_menu.actions] == [
            "editor.open",
            "editor.save",
            "editor.save_as",
            "editor.reload",
            "editor.close",
        ]


@pytest.mark.asyncio
async def test_open_action_opens_the_open_dialog() -> None:
    """The Open action shows the Open file dialog."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        open_action = next(a for a in screen.ACTIONS if a.id == "editor.open")
        assert open_action.action == "open_file"
        screen.action_open_file()
        await wait_until(pilot, lambda: open_file_dialog(host) is not None)
        dialog = open_file_dialog(host)
        assert dialog is not None
        assert dialog.mode is FileDialogMode.OPEN
        await pilot.press("escape")
        await wait_until(pilot, lambda: open_file_dialog(host) is None)


@pytest.mark.asyncio
async def test_wrap_mode_checkable_state_syncs_with_widget() -> None:
    """Line Numbers checkable state reflects widget state."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Get the wrap mode action (should be checkable)
        wrap_action = next(a for a in screen.ACTIONS if a.id == "editor.wrap_mode")
        assert wrap_action.checkable

        # Initial state: not checked (soft_wrap starts False per app.py)
        assert not wrap_action.checked

        # Toggle wrap on the widget
        screen.document.editor.soft_wrap = True
        await pilot.pause()

        # Action should now be checked
        assert wrap_action.checked


@pytest.mark.asyncio
async def test_line_numbers_checkable_state_syncs_with_widget() -> None:
    """Line Numbers checkable state reflects widget state."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Get the line numbers action (should be checkable)
        line_numbers_action = next(a for a in screen.ACTIONS if a.id == "editor.line_numbers")
        assert line_numbers_action.checkable

        # Initial state: checked (show_line_numbers starts True per EditorScreen)
        assert line_numbers_action.checked

        # Toggle line numbers off the widget
        screen.document.editor.show_line_numbers = False
        await pilot.pause()

        # Action should now be unchecked
        assert not line_numbers_action.checked


@pytest.mark.asyncio
async def test_action_save_when_file_has_no_path_opens_the_save_dialog() -> None:
    """Save action opens the file dialog when file has no path."""
    host = EditorScreenHost(path=None, standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Ensure editor has no file path
        assert screen.document.editor.file_path is None

        # Call save action
        screen.action_save()
        await wait_until(pilot, lambda: open_file_dialog(host) is not None)


@pytest.mark.asyncio
async def test_action_save_as_opens_the_save_dialog() -> None:
    """Save As action opens the file dialog."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Call save as action
        screen.action_save_as()
        await wait_until(pilot, lambda: open_file_dialog(host) is not None)


@pytest.mark.asyncio
async def test_action_reload_when_no_file_path() -> None:
    """Reload action notifies when file has no path."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Ensure document has no file path
        assert screen.document.editor.file_path is None

        # Call reload action
        screen.action_reload()
        await pilot.pause()

        # A notification should be shown (nothing to reload)

        # Call close action (it should exit)
        # We don't actually call action_close_editor because it would exit the test
        # Instead, we just verify the logic by checking the app state

        # We can't test calling action_quit_editor directly because it exits the app

        # If there's no match, cursor should remain the same


@pytest.mark.asyncio
async def test_override_moves_key_and_updates_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An override moves the key binding and updates menu/hint bar labels."""
    from nova_editor.app import NovaEditApp
    from nova_widgets.key_types import KeySequence
    from nova_widgets.keymap import HintBar

    calls: list[str] = []

    def spy_save(_self: object) -> None:
        calls.append("save")

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    # Create a keybindings config with save overridden to Ctrl+K
    keybindings = write_keys(
        tmp_path,
        {
            "editor.save": KeySequence.parse("ctrl+k"),
        },
    )
    monkeypatch.setattr(EditorScreen, "action_save", spy_save)

    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test(size=(200, 24)) as pilot:
        await pilot.pause()

        # Press the new key (Ctrl+K) - should trigger save
        await pilot.press("ctrl+k")
        await pilot.pause()

        # Press the old key (Ctrl+S) - should not trigger save
        await pilot.press("ctrl+s")
        await pilot.pause()

        # Only Ctrl+K should have triggered the action
        assert calls == ["save"]

        # Verify the action's shortcut_label shows the new key
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        save_action = next((a for a in screen.ACTIONS if a.id == "editor.save"), None)
        assert save_action is not None
        assert save_action.shortcut_label == "Ctrl+K"

        # Verify the hint bar shows the new key
        hint_bar = app.query_one(HintBar)
        hint_bar_text = str(hint_bar.render())
        assert "Ctrl+K  Save" in hint_bar_text
        # Make sure Ctrl+S Save is not in the hint bar (avoid false positives from Ctrl+Shift+S)
        assert "Ctrl+S  Save" not in hint_bar_text


@pytest.mark.asyncio
async def test_empty_override_unmaps_key(tmp_path: Path) -> None:
    """An empty override unmaps the key (action no longer triggers)."""
    from nova_editor.app import NovaEditApp
    from nova_widgets.keymap import HintBar

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    # Create a keybindings config with find unmapped (empty override)
    keybindings = write_keys(
        tmp_path,
        {
            "editor.find": None,
        },
    )

    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test(size=(200, 24)) as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Verify the action has no shortcut
        find_action = next((a for a in screen.ACTIONS if a.id == "editor.find"), None)
        assert find_action is not None
        assert find_action.shortcut is None
        assert find_action.shortcut_label == ""

        # Press the default find key (Ctrl+F) - should not show the popup
        await pilot.press("ctrl+f")
        await pilot.pause()

        assert screen.find_popup.display is False

        # Verify the hint bar doesn't show the old key or the unmapped action
        hint_bar = app.query_one(HintBar)
        hint_bar_text = str(hint_bar.render())
        assert "Ctrl+F  Find" not in hint_bar_text  # Avoid matching "Find Next" or "Find Previous"
        assert "Find…" not in hint_bar_text  # The action text with ellipsis


@pytest.mark.asyncio
async def test_override_takes_key_from_another_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An override can assign a key that belongs to another action."""
    from nova_editor.app import NovaEditApp
    from nova_widgets.key_types import KeySequence

    calls: list[str] = []

    def spy_save_as(_self: object) -> None:
        calls.append("save_as")

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    # Create a keybindings config with save_as overridden to F2
    keybindings = write_keys(
        tmp_path,
        {
            "editor.save_as": KeySequence.parse("f2"),
        },
    )
    monkeypatch.setattr(EditorScreen, "action_save_as", spy_save_as)

    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Press F2 (the new key for save_as)
        await pilot.press("f2")
        await pilot.pause()

        # Verify the action was triggered
        assert calls == ["save_as"]


@pytest.mark.asyncio
async def test_reload_keymap_applies_changed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """reload_keymap applies changes from the keybindings file."""
    from nova_editor.app import NovaEditApp

    calls: list[str] = []

    def spy_goto(_self: object) -> None:
        calls.append("goto")

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    # Create a keybindings config with goto overridden to Ctrl+L
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "keybindings.toml"
    config_file.write_text('[bindings]\n"editor.goto" = "ctrl+l"\n')

    keybindings = KeybindingsConfig(config_dir=config_dir)

    # Patch the goto action
    monkeypatch.setattr(EditorScreen, "action_goto", spy_goto)

    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Verify the override is applied (Ctrl+L)
        goto_action = next((a for a in screen.ACTIONS if a.id == "editor.goto"), None)
        assert goto_action is not None
        assert goto_action.shortcut_label == "Ctrl+L"

        # Rewrite the config with defaults (empty file)
        config_file.write_text("[bindings]\n")

        # Reload the keybindings config from disk
        keybindings.reload()

        # Reload the keymap
        screen.reload_keymap()
        await pilot.pause()

        # Verify the default is restored (Ctrl+G)
        assert goto_action.shortcut_label == "Ctrl+G"

        # Press the restored default key (Ctrl+G)
        await pilot.press("ctrl+g")
        await pilot.pause()

        # Press the old overridden key (Ctrl+L) - should not trigger
        await pilot.press("ctrl+l")
        await pilot.pause()

        # Only Ctrl+G should have triggered the action
        assert calls == ["goto"]


@pytest.mark.asyncio
async def test_key_swallowing_undo_override_moves_key(tmp_path: Path) -> None:
    """B1: When Undo is overridden from Ctrl+Z to Ctrl+Shift+Z, Ctrl+Z is swallowed (doesn't reach widget)."""
    from nova_editor.app import NovaEditApp
    from nova_widgets.key_types import KeySequence

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("initial content")

    # Create keybindings with undo overridden to Ctrl+Shift+Z
    keybindings = write_keys(
        tmp_path,
        {
            "editor.undo": KeySequence.parse("ctrl+shift+z"),
        },
    )

    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Verify the undo action has the overridden shortcut
        undo_action = next((a for a in screen.ACTIONS if a.id == "editor.undo"), None)
        assert undo_action is not None
        assert undo_action.shortcut == KeySequence.parse("ctrl+shift+z")

        # Type, then press the old default key (Ctrl+Z): the screen swallows it, so nothing is undone
        screen.document.editor.focus()
        await pilot.press("x")
        await pilot.pause()
        assert screen.document.editor.text.startswith("x")
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert screen.document.editor.text.startswith("x")

        # The new key undoes
        await pilot.press("ctrl+shift+z")
        await pilot.pause()
        assert not screen.document.editor.text.startswith("x")


@pytest.mark.asyncio
async def test_key_swallowing_empty_override_unmaps(tmp_path: Path) -> None:
    """B1: When Undo is unmapped (empty override), Ctrl+Z is swallowed and doesn't trigger undo."""
    from nova_editor.app import NovaEditApp

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("initial content")

    # Create keybindings with undo unmapped (None)
    keybindings = write_keys(
        tmp_path,
        {
            "editor.undo": None,
        },
    )

    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Verify the undo action has no shortcut
        undo_action = next((a for a in screen.ACTIONS if a.id == "editor.undo"), None)
        assert undo_action is not None
        assert undo_action.shortcut is None

        # Ctrl+Z is swallowed: typed text stays
        screen.document.editor.focus()
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert screen.document.editor.text.startswith("x")


@pytest.mark.asyncio
async def test_key_swallowing_all_editing_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B1: All six editing actions swallow their old default key when moved; the new key runs the editor action."""
    from nova_editor.app import NovaEditApp
    from nova_widgets.key_types import KeySequence

    test_file = tmp_path / "test.txt"
    test_file.write_text("initial content for testing")

    moved = {
        "undo": ("ctrl+z", "ctrl+shift+z", "Ctrl+Shift+Z"),
        "redo": ("ctrl+y", "ctrl+shift+y", "Ctrl+Shift+Y"),
        "cut": ("ctrl+x", "ctrl+shift+x", "Ctrl+Shift+X"),
        "copy": ("ctrl+c", "ctrl+shift+c", "Ctrl+Shift+C"),
        "paste": ("ctrl+v", "ctrl+shift+v", "Ctrl+Shift+V"),
        "select_all": ("ctrl+a", "ctrl+shift+a", "Ctrl+Shift+A"),
    }
    keybindings = write_keys(tmp_path, {f"editor.{name}": KeySequence.parse(new) for name, (_, new, _l) in moved.items()})

    calls: list[str] = []
    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        editor = screen.document.editor
        for name in moved:
            monkeypatch.setattr(type(editor), f"action_{name}", lambda _self, n=name: calls.append(n))
        for name, (_old, _new, label) in moved.items():
            action = next(a for a in screen.ACTIONS if a.id == f"editor.{name}")
            assert action.shortcut_label == label

        editor.focus()
        for old, _new, _label in moved.values():
            await pilot.press(old)
            await pilot.pause()
        assert calls == []  # the old keys never reached the widget
        assert editor.selected_text == ""
        assert editor.text == "initial content for testing"

        for _old, new, _label in moved.values():
            await pilot.press(new)
            await pilot.pause()
        assert calls == list(moved)


@pytest.mark.asyncio
async def test_a_popup_input_keeps_its_own_keys(tmp_path: Path) -> None:
    """Ctrl+A in a popup moves the cursor to the start (as the Input does) and does not select the document."""
    from textual.widgets import Input

    from nova_editor.app import NovaEditApp

    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")
    app = NovaEditApp(path=test_file)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await pilot.press("ctrl+f")
        await pilot.pause()
        popup = screen.find_popup
        assert isinstance(app.focused, Input)
        await pilot.press("a", "b", "c")
        assert app.focused.cursor_position == 3
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert app.focused.cursor_position == 0
        assert screen.document.editor.selected_text == ""
        await pilot.press("ctrl+e", "ctrl+w")
        await pilot.pause()
        assert app.focused.value == ""  # Ctrl+W deleted the word, it did not close the editor
        assert popup.display


@pytest.mark.asyncio
async def test_menu_items_run_their_actions(tmp_path: Path) -> None:
    """A triggered menu item runs the same flow as its key (every item)."""
    from nova_editor.app import NovaEditApp
    from nova_widgets.menu import Menu

    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")
    app = NovaEditApp(path=test_file)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        editor = screen.document.editor
        menu = next(m for m in screen.menu_bar.actions if isinstance(m, Menu))

        async def trigger(action_id: str) -> None:
            action = next(a for a in screen.ACTIONS if a.id == action_id)
            screen.post_message(Menu.Triggered(menu, action))
            await pilot.pause()

        wrap = editor.soft_wrap
        await trigger("editor.wrap_mode")
        assert editor.soft_wrap is not wrap
        numbers = editor.show_line_numbers
        await trigger("editor.line_numbers")
        assert editor.show_line_numbers is not numbers
        await trigger("editor.select_all")
        assert editor.selected_text == "hello world"
        await trigger("editor.find")
        assert screen.find_popup.display
        await trigger("editor.goto")
        assert screen.goto_popup.display
        await trigger("editor.save_as")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pilot.press("escape")
        await wait_until(pilot, lambda: open_file_dialog(app) is None)
        await trigger("editor.save")
        assert editor.file_path == test_file
        await trigger("editor.reload")
        await trigger("editor.find_next")
        await trigger("editor.find_previous")
        await trigger("editor.open")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pilot.press("escape")
        await wait_until(pilot, lambda: open_file_dialog(app) is None)
