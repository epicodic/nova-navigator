"""`NovaTextArea` with the first-content timing hook of the benchmark harness."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal, cast

from rich.console import RenderableType
from rich.segment import Segment
from textual.content import Content
from textual.strip import Strip

from .core import ByteSource
from .document._lazy_config import LazyConfig
from .document._lazy_document import LazyDocument
from .widget import NovaTextArea


class TimedNovaTextArea(NovaTextArea):
    """NovaTextArea subclass that tracks first content render for timing hook."""

    def __init__(
        self,
        text: str = "",
        *,
        language: str | None = None,
        theme: str = "css",
        soft_wrap: bool = False,
        tab_behavior: Literal["focus", "indent"] = "focus",
        read_only: bool = False,
        show_cursor: bool = True,
        show_line_numbers: bool = False,
        line_number_start: int = 1,
        max_checkpoints: int | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: RenderableType | None = None,
        compact: bool = False,
        highlight_cursor_line: bool = True,
        placeholder: str | Content = "",
        timing_file: str | None = None,
        _prebuilt_document: LazyDocument | None = None,
    ) -> None:
        super().__init__(
            text=text,
            language=language,
            theme=theme,
            soft_wrap=soft_wrap,
            tab_behavior=tab_behavior,
            read_only=read_only,
            show_cursor=show_cursor,
            show_line_numbers=show_line_numbers,
            line_number_start=line_number_start,
            max_checkpoints=max_checkpoints,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            tooltip=tooltip,
            compact=compact,
            highlight_cursor_line=highlight_cursor_line,
            placeholder=placeholder,
            _prebuilt_document=_prebuilt_document,
        )
        self.timing_file = timing_file
        self._timing_first_content_written = False

    @classmethod
    def open(
        cls,
        source: Path | str | ByteSource,
        *,
        language: str | None = None,
        soft_wrap: bool = False,
        config: LazyConfig | None = None,
        highlight_limit: int = 1_048_576,
        timing_file: str | None = None,
        **kwargs: Any,
    ) -> TimedNovaTextArea:
        """Open a file lazily with timing support.

        Args:
            source: A path or a ByteSource.
            language: Language to highlight.
            soft_wrap: Start with soft wrapping.
            config: Thresholds of the lazy document.
            highlight_limit: Largest source (bytes) that is highlighted.
            timing_file: Optional file path for timing hooks.
            **kwargs: Additional parameters passed to the constructor.

        Returns:
            The widget, ready to be mounted.
        """
        widget = cast(
            "TimedNovaTextArea",
            super().open(
                source,
                language=language,
                soft_wrap=soft_wrap,
                config=config,
                highlight_limit=highlight_limit,
                **kwargs,
            ),
        )
        widget.timing_file = timing_file
        return widget

    def render_line(self, y: int) -> Strip:
        """Override render_line to implement timing hook on first content."""
        result = super().render_line(y)

        # Check if this line has non-space content
        # result is a Strip object, check if it has non-empty cells
        if self.timing_file and not self._timing_first_content_written:
            # Convert Strip to string to check for content
            try:
                # Check if Strip has any non-whitespace content
                has_content = False
                for segment in result:
                    if isinstance(segment, Segment) and segment.text.strip():
                        has_content = True
                        break

                if has_content:
                    ns = time.perf_counter_ns()
                    with open(self.timing_file, "w") as f:
                        f.write(f"FIRST_CONTENT {ns}\n")
                    self._timing_first_content_written = True
            except (OSError, ValueError, AttributeError):
                # Silently ignore if we can't write or process
                pass

        return result
