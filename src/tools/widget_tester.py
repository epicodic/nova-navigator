"""Widget tester — display all standard UI components.

Usage:
  uv run widget_tester
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Label, Rule, Static

from nova_widgets import Button, Checkbox, Input, Select
from nova_widgets.menu import MenuBar
from nova_widgets.menu import constructor as mc

_SELECT_OPTIONS: list[tuple[str, str]] = [
    ("Alpha", "alpha"),
    ("Beta", "beta"),
    ("Gamma", "gamma"),
    ("Delta", "delta"),
]


class WidgetTesterApp(App[None]):
    """Developer tool: all standard UI widgets rendered with the app stylesheet."""

    TITLE = "Nova Navigator — Widget Tester"
    BINDINGS: ClassVar = [("q", "quit", "Quit")]

    DEFAULT_CSS = """
    WidgetTesterApp {
        #btn-row Button { width: auto; min-width: 16; }

        VerticalScroll { height: 1fr; }

        .section-title {
            text-style: bold underline;
            padding: 1 2 0 2;
        }
        .row {
            height: auto;
            padding: 0 2 1 2;
            border-bottom: solid $panel-lighten-1;
        }
        .row > * { margin-right: 2; }
        .row Select { width: 24; }
        .row Input  { width: 30; }

        #result {
            height: 1;
            color: $success;
            padding: 0 2;
        }
    }
    """

    def _build_menu_bar(self) -> MenuBar:
        bar = MenuBar()
        file_menu = bar.add_menu("File")
        file_menu.add_action("New", shortcut="Ctrl+N")
        file_menu.add_action("Open…", shortcut="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_action("Quit", shortcut="Ctrl+Q")

        edit_menu = bar.add_menu("Edit")
        edit_menu.add_action("Undo", shortcut="Ctrl+Z")
        edit_menu.add_action("Redo", shortcut="Ctrl+Y")
        edit_menu.add_separator()
        sub = edit_menu.add_menu("Find")
        sub.add_action("Find…", shortcut="Ctrl+F")
        sub.add_action("Replace…", shortcut="Ctrl+H")

        opts_menu = bar.add_menu("Options")
        opts_menu.add(
            mc.action("Show hidden files", checkable=True),
            mc.separator(),
            *mc.group(
                mc.action("Sort by name", checkable=True),
                mc.action("Sort by size", checkable=True),
                mc.action("Sort by date", checkable=True),
            ),
        )
        return bar

    def compose(self) -> ComposeResult:
        yield self._build_menu_bar()

        with VerticalScroll():
            # ── Buttons ──────────────────────────────────────────────────────
            yield Label("Buttons", classes="section-title")
            with Horizontal(classes="row", id="btn-row"):
                yield Button("Default")
                yield Button("Primary", variant="primary")
                yield Button("Success", variant="success")
                yield Button("Warning", variant="warning")
                yield Button("Error", variant="error")
                yield Button("Disabled", disabled=True)

            # ── Input boxes ──────────────────────────────────────────────────
            yield Label("Input boxes", classes="section-title")
            with Horizontal(classes="row"):
                yield Input(placeholder="Plain text")
                yield Input(placeholder="Password", password=True)
                yield Input(value="Pre-filled value")
                yield Input(placeholder="Disabled", disabled=True)

            # ── Select (combo-boxes) ─────────────────────────────────────────
            yield Label("Select (combo-box)", classes="section-title")
            with Horizontal(classes="row"):
                yield Select(options=_SELECT_OPTIONS, prompt="Choose…")
                yield Select(options=_SELECT_OPTIONS, value="beta")
                yield Select(options=_SELECT_OPTIONS, disabled=True, prompt="Disabled")

            # ── Checkboxes ───────────────────────────────────────────────────
            yield Label("Checkboxes", classes="section-title")
            with Horizontal(classes="row"):
                yield Checkbox("Unchecked")
                yield Checkbox("Checked", value=True)
                yield Checkbox("Disabled unchecked", disabled=True)
                yield Checkbox("Disabled checked", value=True, disabled=True)

            # ── Menus ─────────────────────────────────────────────────────────
            yield Label("Menus", classes="section-title")
            yield Static(
                "  Use the menu bar at the top. It includes submenus, separators, checkable items, and radio groups.",
                classes="row",
            )

        yield Rule()
        yield Static("", id="result")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.query_one("#result", Static).update(f"Button pressed: {event.button.label!r}")

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#result", Static).update(f"Input changed: {event.value!r}")

    def on_select_changed(self, event: Select.Changed) -> None:
        self.query_one("#result", Static).update(f"Select changed: {event.value!r}")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self.query_one("#result", Static).update(f"Checkbox {event.checkbox.label!r}: {event.value}")


def main() -> None:
    WidgetTesterApp().run()


if __name__ == "__main__":
    main()
