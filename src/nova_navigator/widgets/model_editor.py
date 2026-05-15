"""ModelEditor — generic widget that renders a flat BaseModel as an editable form."""

from __future__ import annotations

import dataclasses
from typing import Any, get_type_hints

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Label

from nova_navigator.config.model import BaseModel
from nova_navigator.widgets._utils import _title_case
from nova_widgets import Checkbox, Input

_PRIMITIVE_TYPES: frozenset[type] = frozenset({bool, str, int, float})


def _is_base_model_type(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, BaseModel)


class ModelEditor(Widget):
    """Widget that auto-renders all primitive fields of a BaseModel as labelled rows.

    Each row contains: a title-cased field name label, a type-appropriate
    control (Checkbox for bool, Input for str/int/float), and a dimmed comment
    label taken from field_comment metadata.

    All rows share CSS class selectors so columns are left-aligned across rows.

    After the user edits values, call :meth:`apply` to write them back to a
    target model instance.
    """

    DEFAULT_CSS = """
    ModelEditor {
        height: auto;

        .me-row {
            height: 3;
        }

        .me-name {
            width: 24;
            padding-top: 1;
        }

        .me-control {
            width: 20;
        }

        .me-comment {
            width: 1fr;
            padding-top: 1;
            color: $text-muted;
        }
    }
    """

    _field_controls: dict[str, Checkbox | Input]
    _field_types: dict[str, type]

    def __init__(self, model: BaseModel, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._field_controls = {}
        self._field_types = {}

    def compose(self) -> ComposeResult:
        rows: list[Widget] = []
        hints = get_type_hints(type(self._model))

        for f in dataclasses.fields(type(self._model)):
            # Skip special metadata fields
            if "self_factory" in f.metadata or "toml_key" in f.metadata:
                continue

            field_type = hints.get(f.name)
            if field_type is None:
                continue

            # Skip nested BaseModel sub-sections
            if _is_base_model_type(field_type):
                continue

            # Only render the four supported primitives
            if field_type not in _PRIMITIVE_TYPES:
                continue

            current_value = getattr(self._model, f.name)
            comment = str(f.metadata.get("toml_comment", ""))

            if field_type is bool:
                control: Checkbox | Input = Checkbox(value=bool(current_value), classes="me-control")
            elif field_type is int:
                control = Input(value=str(current_value), type="integer", classes="me-control")
            elif field_type is float:
                control = Input(value=str(current_value), type="number", classes="me-control")
            else:  # str
                control = Input(value=str(current_value), classes="me-control")

            self._field_controls[f.name] = control
            self._field_types[f.name] = field_type

            rows.append(
                Horizontal(
                    Label(_title_case(f.name), classes="me-name"),
                    control,
                    Label(comment, classes="me-comment"),
                    classes="me-row",
                )
            )

        yield Vertical(*rows)

    def apply(self, target: BaseModel) -> None:
        """Write current control values back into *target*.

        Coerces each value to the field's declared Python primitive type.
        For numeric Input fields, falls back to the existing target value on
        ValueError (e.g. if the user left the field empty).
        """
        for field_name, control in self._field_controls.items():
            field_type = self._field_types[field_name]
            if isinstance(control, Checkbox):
                value: Any = bool(control.value)
            elif field_type is int:
                try:
                    value = int(control.value)
                except ValueError:
                    value = getattr(target, field_name)
            elif field_type is float:
                try:
                    value = float(control.value)
                except ValueError:
                    value = getattr(target, field_name)
            else:
                value = control.value
            object.__setattr__(target, field_name, value)
