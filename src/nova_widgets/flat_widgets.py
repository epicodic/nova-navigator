"""Flat-style widget variants for Nova Navigator."""

from __future__ import annotations

from textual.widgets import Button as _Button
from textual.widgets import Checkbox as _Checkbox
from textual.widgets import Input as _Input
from textual.widgets import Select as _Select
from textual.widgets._button import ButtonVariant


class Button(_Button):
    """A ``Button`` that always uses the flat look with Nova Navigator styling.

    Equivalent to ``Button(..., flat=True)`` but also ships the modified
    focus/hover CSS.
    """

    DEFAULT_CSS = """
    Button.-style-flat:focus {
        color: $text;
        background: $primary;
        border: block $primary;
        text-style: none;
    }
    Button.-style-flat.-primary:focus {
        color: $text;
        background: $primary;
        border: block $primary;
        text-style: none;
    }
    Button.-style-flat.-success:focus {
        color: $text;
        background: $success;
        border: block $success;
        text-style: none;
    }
    Button.-style-flat.-warning:focus {
        color: $text;
        background: $warning;
        border: block $warning;
        text-style: none;
    }
    Button.-style-flat.-error:focus {
        color: $text;
        background: $error;
        border: block $error;
        text-style: none;
    }
    Button.-style-flat:hover {
        color: $text;
        background: $primary-lighten-2;
        border: block $primary-lighten-2;
    }
    Button.-style-flat.-primary:hover {
        color: $text;
        background: $primary-lighten-2;
        border: block $primary-lighten-2;
    }
    Button.-style-flat.-success:hover {
        color: $text;
        background: $success-lighten-2;
        border: block $success-lighten-2;
    }
    Button.-style-flat.-warning:hover {
        color: $text;
        background: $warning-lighten-2;
        border: block $warning-lighten-2;
    }
    Button.-style-flat.-error:hover {
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
        compact: bool = False,
    ) -> None:
        super().__init__(
            label,
            variant,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            flat=True,
            compact=compact,
        )


class Input(_Input):
    """An ``Input`` with the Nova Navigator flat surface border style."""

    DEFAULT_CSS = """
    Input {
        border: inner $surface;

        &:focus {
            background: $primary 25%;
            border: inner $primary 25%;
        }
    }
    """


class Select(_Select[object]):
    """A ``Select`` with the Nova Navigator flat surface border style."""

    DEFAULT_CSS = """
    Select > SelectCurrent {
        border: inner $surface;
    }
    Select:focus > SelectCurrent {
        background: $primary 25%;
        border: inner $primary 25%;
    }
    """


class Checkbox(_Checkbox):
    """A ``Checkbox`` with no border and no background tint on focus."""

    DEFAULT_CSS = """
    Checkbox {
        border: inner transparent;
        background: transparent;
        margin: 0;
        padding: 0;

        &:focus {
            background: $primary 25%;
            border: inner $primary 25%;
        }
    }
    """
