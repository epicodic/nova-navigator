"""Flat-style widget variants for Nova Navigator."""

from __future__ import annotations

from textual.widgets import Button
from textual.widgets._button import ButtonVariant


class FlatButton(Button):
    """A ``Button`` that always uses the flat look with Nova Navigator styling.

    Equivalent to ``Button(..., flat=True)`` but also ships the modified
    focus/hover CSS.
    """

    DEFAULT_CSS = """
    FlatButton.-style-flat:focus {
        color: $text;
        background: $primary;
        border: block $primary;
        text-style: none;
    }
    FlatButton.-style-flat.-primary:focus {
        color: $text;
        background: $primary;
        border: block $primary;
        text-style: none;
    }
    FlatButton.-style-flat.-success:focus {
        color: $text;
        background: $success;
        border: block $success;
        text-style: none;
    }
    FlatButton.-style-flat.-warning:focus {
        color: $text;
        background: $warning;
        border: block $warning;
        text-style: none;
    }
    FlatButton.-style-flat.-error:focus {
        color: $text;
        background: $error;
        border: block $error;
        text-style: none;
    }
    FlatButton.-style-flat:hover {
        color: $text;
        background: $primary-lighten-2;
        border: block $primary-lighten-2;
    }
    FlatButton.-style-flat.-primary:hover {
        color: $text;
        background: $primary-lighten-2;
        border: block $primary-lighten-2;
    }
    FlatButton.-style-flat.-success:hover {
        color: $text;
        background: $success-lighten-2;
        border: block $success-lighten-2;
    }
    FlatButton.-style-flat.-warning:hover {
        color: $text;
        background: $warning-lighten-2;
        border: block $warning-lighten-2;
    }
    FlatButton.-style-flat.-error:hover {
        color: $text;
        background: $error-lighten-2;
        border: block $error-lighten-2;
    }
    """

    def __init__(
        self,
        label: str | None = None,
        variant: ButtonVariant = "default",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            label,
            variant,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            flat=True,
        )
