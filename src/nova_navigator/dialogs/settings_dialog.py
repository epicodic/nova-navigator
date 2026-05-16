"""SettingsDialog — modal dialog for editing all application settings."""

from __future__ import annotations

import dataclasses
from typing import get_type_hints

from textual.app import ComposeResult
from textual.widgets import TabbedContent, TabPane

from nova_navigator.config.model import BaseModel
from nova_navigator.config.settings import Settings
from nova_navigator.widgets._utils import _title_case
from nova_navigator.widgets.model_editor import ModelEditor

from .dialog import DefaultButton, Dialog


class SettingsDialog(Dialog):
    """Modal dialog that exposes all Settings sections as tabs of editable rows."""

    DEFAULT_CSS = """
    SettingsDialog {
        #dialog_box {
            width: 70%;
            height: 60%;
        }

        TabbedContent {
            height: 1fr;
        }
    }
    """

    _config: Settings
    _editors: dict[str, ModelEditor]

    def __init__(self, settings: Settings) -> None:
        super().__init__(title="Settings", buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._config = settings
        self._editors = {}

    @property
    def config(self) -> Settings:
        return self._config

    def compose_content(self) -> ComposeResult:
        hints = get_type_hints(type(self._config))
        tc = TabbedContent()
        for f in dataclasses.fields(type(self._config)):
            field_type = hints.get(f.name)
            if not (field_type and isinstance(field_type, type) and issubclass(field_type, BaseModel)):
                continue
            section_value = getattr(self._config, f.name)
            editor = ModelEditor(section_value, id=f"editor_{f.name}")
            self._editors[f.name] = editor
            tc.compose_add_child(TabPane(_title_case(f.name), editor))
        yield tc

    def action_accept_dialog(self) -> None:
        for field_name, editor in self._editors.items():
            editor.apply(getattr(self._config, field_name))
        super().action_accept_dialog()
