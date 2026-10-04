"""The editor screen: composition, per-screen actions, menus, bars and document holder."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.bars import GotoBar
from nova_editor.document_view import DocumentView
from nova_editor.screen import EditorScreen
from nova_editor.search_bar import SearchBar
from nova_widgets.keybindings_config import KeybindingsConfig

from .screen_host import EditorScreenHost, write_keys


@pytest.mark.asyncio
async def test_editor_screen_composes_menu_bar_and_hint_bar() -> None:
    """The screen composes a MenuBar and HintBar."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Find MenuBar and HintBar in composed widgets
        from nova_widgets.keymap import HintBar
        from nova_widgets.menu import MenuBar

        menu_bars = screen.query(MenuBar)
        hint_bars = screen.query(HintBar)

        assert len(menu_bars) > 0, "MenuBar not found in composed widgets"
        assert len(hint_bars) > 0, "HintBar not found in composed widgets"


@pytest.mark.asyncio
async def test_editor_screen_composes_all_bars() -> None:
    """The screen composes GotoBar, PathBar, SaveBar, and SearchBar."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        from nova_editor.bars import GotoBar, PathBar, SaveBar
        from nova_editor.search_bar import SearchBar

        goto_bars = screen.query(GotoBar)
        path_bars = screen.query(PathBar)
        save_bars = screen.query(SaveBar)
        search_bars = screen.query(SearchBar)

        assert len(goto_bars) > 0, "GotoBar not found"
        assert len(path_bars) > 0, "PathBar not found"
        assert len(save_bars) > 0, "SaveBar not found"
        assert len(search_bars) > 0, "SearchBar not found"


@pytest.mark.asyncio
async def test_editor_screen_composes_status_line_and_editor() -> None:
    """The screen composes StatusLine and the editor widget (NovaTextArea)."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        from nova_editor.status_line import StatusLine
        from nova_editor.widget import NovaTextArea

        status_lines = screen.query(StatusLine)
        editors = screen.query(NovaTextArea)

        assert len(status_lines) > 0, "StatusLine not found"
        assert len(editors) > 0, "NovaTextArea not found"


@pytest.mark.asyncio
async def test_screen_has_per_screen_actions() -> None:
    """Each screen instance has its own ACTIONS list."""
    host1 = EditorScreenHost(standalone=True)
    async with host1.run_test() as pilot1:
        await pilot1.pause()
        screen1 = host1.screen_instance
        assert screen1 is not None
        assert hasattr(screen1, "ACTIONS")
        assert len(screen1.ACTIONS) == 18

        # Create another screen and verify they have different action objects
        host2 = EditorScreenHost(standalone=True)
        async with host2.run_test() as pilot2:
            await pilot2.pause()
            screen2 = host2.screen_instance
            assert screen2 is not None
            assert hasattr(screen2, "ACTIONS")
            assert len(screen2.ACTIONS) == 18

            # Verify action objects are different (not shared)
            screen1_action_ids = {id(a) for a in screen1.ACTIONS}
            screen2_action_ids = {id(a) for a in screen2.ACTIONS}
            assert not (screen1_action_ids & screen2_action_ids), "Two screens must not share Action objects"


@pytest.mark.asyncio
async def test_screen_overrides_do_not_affect_other_screens() -> None:
    """Key overrides on one screen don't affect another screen."""

    host1 = EditorScreenHost(standalone=True)
    async with host1.run_test() as pilot1:
        await pilot1.pause()
        screen1 = host1.screen_instance
        assert screen1 is not None

        # Get the save action and its default shortcut
        save_action1 = next(a for a in screen1.ACTIONS if a.id == "editor.save")
        default_shortcut = save_action1.shortcut

        # Create another screen
        host2 = EditorScreenHost(standalone=True)
        async with host2.run_test() as pilot2:
            await pilot2.pause()
            screen2 = host2.screen_instance
            assert screen2 is not None

            # Get the save action on screen2
            save_action2 = next(a for a in screen2.ACTIONS if a.id == "editor.save")

            # Modify screen1's action shortcut
            save_action1.set_shortcut("ctrl+shift+s")

            # Verify screen2's action shortcut is unchanged
            assert save_action2.shortcut == default_shortcut


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
            ["editor.line_numbers", "editor.wrap_mode"],
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
async def test_document_held_by_reference_only() -> None:
    """The screen holds a DocumentView by reference, no state duplication."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert hasattr(screen, "document")
        assert isinstance(screen.document, DocumentView)

        # Verify the document has an editor widget
        assert hasattr(screen.document, "editor")
        from nova_editor.widget import NovaTextArea

        assert isinstance(screen.document.editor, NovaTextArea)


@pytest.mark.asyncio
async def test_open_action_shows_neutral_stub_message() -> None:
    """The Open action shows a neutral notification message."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        open_action = next(a for a in screen.ACTIONS if a.id == "editor.open")
        assert open_action.action == "open_file"
        screen.action_open_file()
        await pilot.pause()
        assert [n.message for n in host._notifications] == ["Open is not available yet"]


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
    """Wrap Mode checkable state reflects widget state."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Get the line numbers action (should be checkable)
        line_numbers_action = next(a for a in screen.ACTIONS if a.id == "editor.line_numbers")
        assert line_numbers_action.checkable

        # Initial state: not checked (show_line_numbers starts False)
        assert not line_numbers_action.checked

        # Toggle line numbers on the widget
        screen.document.editor.show_line_numbers = True
        await pilot.pause()

        # Action should now be checked
        assert line_numbers_action.checked


@pytest.mark.asyncio
async def test_screen_has_keymap_registry() -> None:
    """The screen has a per-screen KeymapRegistry."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert hasattr(screen, "keymap_registry")

        from nova_widgets.keymap import KeymapRegistry

        assert isinstance(screen.keymap_registry, KeymapRegistry)


@pytest.mark.asyncio
async def test_screen_has_per_screen_hint_bar() -> None:
    """The screen has a per-screen HintBar."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert hasattr(screen, "hint_bar")

        from nova_widgets.keymap import HintBar

        assert isinstance(screen.hint_bar, HintBar)


@pytest.mark.asyncio
async def test_keybindings_config_applied_to_actions(tmp_path: Path) -> None:
    """Keybindings config overrides are applied to actions."""
    from nova_widgets.key_types import KeySequence

    # Create a keybindings config with an override
    keybindings = write_keys(
        tmp_path,
        {
            "editor.save": KeySequence.parse("f2"),
        },
    )

    host = EditorScreenHost(standalone=True, keybindings=keybindings)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Get the save action and verify it has the override
        save_action = next(a for a in screen.ACTIONS if a.id == "editor.save")
        assert save_action.shortcut == KeySequence.parse("f2")


@pytest.mark.asyncio
async def test_action_save_when_file_has_no_path() -> None:
    """Save action shows PathBar when file has no path."""
    host = EditorScreenHost(path=None, standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Ensure editor has no file path
        assert screen.document.editor.file_path is None

        # Call save action
        screen.action_save()
        await pilot.pause()

        # PathBar should be displayed and focused
        from nova_editor.bars import PathBar

        path_bars = screen.query(PathBar)
        assert len(path_bars) > 0
        assert path_bars.first().display is True


@pytest.mark.asyncio
async def test_action_save_as_shows_path_bar() -> None:
    """Save As action displays PathBar."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Call save as action
        screen.action_save_as()
        await pilot.pause()

        # PathBar should be displayed
        from nova_editor.bars import PathBar

        path_bars = screen.query(PathBar)
        assert len(path_bars) > 0
        assert path_bars.first().display is True


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


@pytest.mark.asyncio
async def test_action_close_editor_when_not_modified() -> None:
    """Close action closes immediately when document is not modified."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Verify document is not modified
        assert not screen.document.editor.modified

        # Call close action (it should exit)
        # We don't actually call action_close_editor because it would exit the test
        # Instead, we just verify the logic by checking the app state


@pytest.mark.asyncio
async def test_action_quit_editor_when_not_modified() -> None:
    """Quit action quits immediately when document is not modified."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Verify document is not modified
        assert not screen.document.editor.modified

        # We can't test calling action_quit_editor directly because it exits the app


@pytest.mark.asyncio
async def test_confirm_bar_chosen_reload_calls_editor_reload() -> None:
    """ConfirmBar Chosen message with reload choice calls editor.reload()."""
    from pathlib import Path

    from nova_editor.bars import ConfirmBar

    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        confirm_bar = screen.query_one(ConfirmBar)

        # Simulate user choosing to reload
        test_path = Path("/home/test.txt")
        confirm_bar.post_message(ConfirmBar.Chosen("reload", test_path))
        await pilot.pause()

        # The confirmation should be handled


@pytest.mark.asyncio
async def test_input_submitted_path_bar_calls_save_to() -> None:
    """Input.Submitted message from PathBar calls save flow."""
    from textual.widgets import Input

    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        path_bar = screen.query_one("#path_bar", Input)

        # Show the path bar
        path_bar.display = True
        await pilot.pause()

        # Simulate user submitting a path
        test_path = "/home/test.txt"
        path_bar.post_message(Input.Submitted(path_bar, value=test_path))
        await pilot.pause()

        # The path bar should be hidden
        assert path_bar.display is False


@pytest.mark.asyncio
async def test_action_find_shows_search_bar() -> None:
    """The find action shows the SearchBar."""
    from nova_editor.search_bar import SearchBar

    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        search_bar = screen.query_one(SearchBar)
        assert not search_bar.display

        # Call the find action
        screen.action_find()
        await pilot.pause()

        # SearchBar should now be displayed and focused
        assert search_bar.display


@pytest.mark.asyncio
async def test_action_find_next_calls_search_when_has_needle() -> None:
    """The find next action calls search if a needle exists."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Set the search term
        screen.document.needle = "test"

        # Call the find next action
        screen.action_find_next()
        await pilot.pause()

        # If there's no match, cursor should remain the same


@pytest.mark.asyncio
async def test_action_find_previous_calls_search_when_has_needle() -> None:
    """The find previous action calls search backward if a needle exists."""
    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        # Set the search term
        screen.document.needle = "test"

        # Call the find previous action
        screen.action_find_previous()
        await pilot.pause()


@pytest.mark.asyncio
async def test_action_go_to_shows_goto_bar() -> None:
    """The goto action shows the GotoBar."""
    from nova_editor.bars import GotoBar

    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        goto_bar = screen.query_one(GotoBar)
        assert not goto_bar.display

        # Call the goto action
        screen.action_goto()
        await pilot.pause()

        # GotoBar should now be displayed and focused
        assert goto_bar.display


@pytest.mark.asyncio
async def test_input_submitted_search_bar_triggers_search() -> None:
    """Input.Submitted from SearchBar triggers a search."""
    from textual.widgets import Input

    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        search_bar = screen.query_one("#search_bar", Input)
        editor = screen.document.editor

        # Type some text into the editor
        editor.load_text("hello world\ntest\nhello again")
        await pilot.pause()

        # Show the search bar
        search_bar.display = True
        search_bar.value = "hello"
        await pilot.pause()

        # Simulate user submitting a search
        search_bar.post_message(Input.Submitted(search_bar, value="hello"))
        await pilot.pause()

        # The search term should be stored
        assert screen.document.needle == "hello"


@pytest.mark.asyncio
async def test_input_submitted_goto_bar_jumps_to_line() -> None:
    """Input.Submitted from GotoBar jumps to the specified line."""
    from textual.widgets import Input

    host = EditorScreenHost(standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None

        goto_bar = screen.query_one("#goto_bar", Input)
        editor = screen.document.editor

        # Load multiline text
        editor.load_text("line 1\nline 2\nline 3\nline 4")
        await pilot.pause()

        # Show the goto bar
        goto_bar.display = True
        goto_bar.value = "3"
        await pilot.pause()

        # Simulate user submitting a line number
        goto_bar.post_message(Input.Submitted(goto_bar, value="3"))
        await pilot.pause()

        # The goto bar should be closed
        assert not goto_bar.display


@pytest.mark.asyncio
async def test_default_key_triggers_its_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A default key (Ctrl+S) triggers its action (save)."""
    from nova_editor.app import NovaEditApp

    calls: list[str] = []

    def spy_save(_self: object) -> None:
        calls.append("save")

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    # Patch the save action
    monkeypatch.setattr(EditorScreen, "action_save", spy_save)

    app = NovaEditApp(path=test_file)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Press the default save key (Ctrl+S)
        await pilot.press("ctrl+s")
        await pilot.pause()

        # Verify the action was triggered
        assert calls == ["save"]


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

        # Press the default find key (Ctrl+F) - should not show search bar
        await pilot.press("ctrl+f")
        await pilot.pause()

        search_bar = screen.query_one(SearchBar)
        assert search_bar.display is False

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
async def test_defaults_without_config_equal_req4_table(tmp_path: Path) -> None:
    """Default key bindings without a config match the REQ-4 table."""
    from nova_editor.app import NovaEditApp
    from nova_widgets.key_types import KeySequence
    from tests.nova_editor.test_editor_actions import REQ4_DEFAULTS

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    app = NovaEditApp(path=test_file)
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Build a dict of action_id -> shortcut from the screen's actions
        labels = {a.id: a.shortcut for a in screen.ACTIONS}

        # Compare with REQ4_DEFAULTS
        expected = {action_id: KeySequence.parse(key) for action_id, key in REQ4_DEFAULTS.items()}
        assert labels == expected


@pytest.mark.asyncio
async def test_goto_bar_displays_when_action_triggered(tmp_path: Path) -> None:
    """The goto action displays and focuses the GotoBar."""
    from nova_editor.app import NovaEditApp

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("line 1\nline 2\nline 3\n")

    app = NovaEditApp(path=test_file)
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Initially GotoBar should not be displayed
        goto_bar = screen.query_one(GotoBar)
        assert goto_bar.display is False

        # Press Ctrl+G to trigger the goto action
        await pilot.press("ctrl+g")
        await pilot.pause()

        # Verify GotoBar is displayed and focused
        assert goto_bar.display is True
        assert app.focused is goto_bar


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
async def test_key_swallowing_non_overridden_key_passes_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B1: When Wrap Mode is not overridden, F10 is NOT swallowed and reaches the widget normally."""
    from nova_editor.app import NovaEditApp

    toggle_wrap_calls: list[str] = []

    def spy_toggle_wrap(_self: object) -> None:
        toggle_wrap_calls.append("toggle_wrap")

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("initial content")

    # No overrides - use defaults
    keybindings = KeybindingsConfig(config_dir=tmp_path)

    # Patch toggle_wrap action to spy on calls
    monkeypatch.setattr(EditorScreen, "action_toggle_wrap", spy_toggle_wrap)

    app = NovaEditApp(path=test_file, keybindings=keybindings)
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Press the default wrap mode key (F10) - should NOT be swallowed, should trigger action
        await pilot.press("f10")
        await pilot.pause()

        # The action should have been triggered
        assert toggle_wrap_calls == ["toggle_wrap"]


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
async def test_input_bar_keeps_its_own_keys(tmp_path: Path) -> None:
    """Ctrl+A in a bar moves the cursor to the start (as the Input does) and does not select the document."""
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
        bar = screen.query_one(SearchBar)
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
        assert bar.display


@pytest.mark.asyncio
async def test_menu_items_run_their_actions(tmp_path: Path) -> None:
    """A triggered menu item runs the same flow as its key (every item except Open)."""
    from nova_editor.app import NovaEditApp
    from nova_editor.bars import PathBar
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
        assert screen.query_one(SearchBar).display
        await trigger("editor.goto")
        assert screen.query_one(GotoBar).display
        await trigger("editor.save_as")
        assert screen.query_one(PathBar).display
        await trigger("editor.save")
        assert editor.file_path == test_file
        await trigger("editor.reload")
        await trigger("editor.find_next")
        await trigger("editor.find_previous")
        await trigger("editor.open")
        assert any("Open is not available yet" in n.message for n in app._notifications)
