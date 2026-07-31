"""Flat-style widget variants for Nova Navigator."""

from __future__ import annotations

from textual.events import Blur, Enter, Focus, Leave
from textual.widgets import Button as _Button
from textual.widgets import Checkbox as _Checkbox
from textual.widgets import Input as _Input
from textual.widgets import Select as _Select
from textual.widgets._button import ButtonVariant

_MIN_HEIGHT_FOR_BORDER = 3
"""A ``border:`` declaration always reserves 1 extra row above and below the
content, regardless of border style. A button shorter than this can't show a
border without its label being swallowed entirely."""


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
        self._border_forced_off = False

    def _on_enter(self, event: Enter) -> None:
        super()._on_enter(event)
        self._sync_hover_focus_border()

    def _on_leave(self, event: Leave) -> None:
        super()._on_leave(event)
        self._sync_hover_focus_border()

    def _on_focus(self, event: Focus) -> None:
        super()._on_focus(event)
        self._sync_hover_focus_border()

    def _on_blur(self, event: Blur) -> None:
        super()._on_blur(event)
        self._sync_hover_focus_border()

    def _sync_hover_focus_border(self) -> None:
        """Suppress the hover/focus border on buttons pinned to a short fixed height.

        The flat button's hover/focus CSS above adds a ``border:`` to signal
        interactivity. Since a border always reserves a row above and below
        the content, a button pinned to ``height: 1`` would have its label
        swallowed entirely the moment a border appeared. Force the border
        back off via an inline style (which always wins over CSS, regardless
        of stylesheet origin or selector specificity) whenever there isn't
        room for one; clear the override otherwise so CSS stays in control.

        Uses the *declared* CSS height (``self.styles.height``), not the
        widget's current rendered ``self.size.height``: for an ``auto``-height
        button, showing/hiding the border changes the rendered height, so
        checking the rendered height would create a feedback loop (hiding the
        border shrinks the height, which then looks "too short" next time,
        keeping it permanently collapsed). A button only needs this override
        when it has an explicit fixed height too small for a border, e.g. a
        caller-supplied ``height: 1`` rule.

        Only touches ``self.styles.border`` when the desired state actually
        changes: reassigning it unconditionally on every enter/leave/focus/blur
        event forces a repaint each time, which for regular auto-height
        buttons served no purpose and caused visible flicker.
        """
        declared_height = self.styles.height
        cells = declared_height.cells if declared_height is not None else None
        is_pinned_too_short = cells is not None and cells < _MIN_HEIGHT_FOR_BORDER
        needs_border_off = is_pinned_too_short and (self.mouse_hover or self.has_focus)
        if needs_border_off and not self._border_forced_off:
            self.styles.border = "none"
            self._border_forced_off = True
        elif not needs_border_off and self._border_forced_off:
            self.styles.border = None
            self._border_forced_off = False


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
