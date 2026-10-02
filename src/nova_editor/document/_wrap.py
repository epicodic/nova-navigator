"""Word-wrap helpers, vendored from Textual 8.2.8.

The upstream files are `textual/_wrap.py`, `textual/_cells.py` (`cell_width_to_column_index`) and `textual/_loop.py` (`loop_last`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from rich.cells import cell_len, get_character_cell_size
from textual.expand_tabs import get_tab_widths

re_chunk = re.compile(r"\S+\s*|\s+")


def _loop_last[T](values: Iterable[T]) -> Iterable[tuple[bool, T]]:
    """Iterate over `values` and yield each one with a flag that is true for the last value."""
    iter_values = iter(values)
    try:
        previous_value = next(iter_values)
    except StopIteration:
        return
    for value in iter_values:
        yield False, previous_value
        previous_value = value
    yield True, previous_value


def cell_width_to_column_index(line: str, cell_width: int, tab_width: int) -> int:
    """Retrieve the column index corresponding to the given cell width.

    Args:
        line: The line of text to search within.
        cell_width: The cell width to convert to column index.
        tab_width: The tab stop width to expand tabs contained within the line.

    Returns:
        The column corresponding to the cell width.
    """
    column_index = 0
    total_cell_offset = 0
    for part, expanded_tab_width in get_tab_widths(line, tab_width):
        # Check if the click landed on a character within this part.
        for character in part:
            total_cell_offset += cell_len(character)
            if total_cell_offset > cell_width:
                return column_index
            column_index += 1

        # Account for the appearance of the tab character for this part
        total_cell_offset += expanded_tab_width
        # Check if the click falls within the boundary of the expanded tab.
        if total_cell_offset > cell_width:
            return column_index

        column_index += 1

    return len(line)


def chunks(text: str) -> Iterable[tuple[int, int, str]]:
    """Yield each chunk of the text as a tuple of start index, end index and content.

    A chunk is a word and any whitespace around it.

    Args:
        text: The text to split into chunks.

    Returns:
        Yields tuples containing the start, end and content for each chunk.
    """
    end = 0
    while (chunk_match := re_chunk.match(text, end)) is not None:
        start, end = chunk_match.span()
        chunk = chunk_match.group(0)
        yield start, end, chunk


def _cumulative_tab_widths(tab_sections: list[tuple[str, int]]) -> list[int]:
    """Return the prefix sum of the tab widths for each codepoint, with one entry past the end."""
    cumulative_width = 0
    cumulative_widths: list[int] = []  # prefix sum of tab widths for each codepoint
    record_widths = cumulative_widths.extend

    for last, (tab_section, tab_width) in _loop_last(tab_sections):
        # add 1 since the \t character is stripped by get_tab_widths
        section_codepoint_length = len(tab_section) + int(bool(tab_width))
        widths = [cumulative_width] * section_codepoint_length
        record_widths(widths)
        cumulative_width += tab_width
        if last:
            cumulative_widths.append(cumulative_width)
    return cumulative_widths


def _fold_chunk(chunk: str, width: int, tab_sections: list[tuple[str, int]], tab_section_index: int) -> tuple[list[str], int]:
    """Split a chunk that is wider than `width` into lines.

    Args:
        chunk: The chunk to fold.
        width: The available cell width.
        tab_sections: The output of `get_tab_widths` for the whole text.
        tab_section_index: The index of the tab section of the first tab in the chunk.

    Returns:
        The folded lines and the tab section index after the chunk.
    """
    _get_character_cell_size = get_character_cell_size
    lines: list[list[str]] = [[]]

    append_new_line = lines.append
    append_to_last_line = lines[-1].append

    total_width = 0
    for character in chunk:
        if character == "\t":
            # Tab characters have dynamic width, so look it up
            cell_width = tab_sections[tab_section_index][1]
            tab_section_index += 1
        else:
            cell_width = _get_character_cell_size(character)

        if total_width + cell_width > width:
            append_new_line([character])
            append_to_last_line = lines[-1].append
            total_width = cell_width
        else:
            append_to_last_line(character)
            total_width += cell_width

    return ["".join(line) for line in lines], tab_section_index


def compute_wrap_offsets(
    text: str,
    width: int,
    tab_size: int,
    fold: bool = True,
    precomputed_tab_sections: list[tuple[str, int]] | None = None,
) -> list[int]:
    """Return the codepoint indices at which `text` is split to fit within `width` cells.

    Args:
        text: The text to examine.
        width: The available cell width.
        tab_size: The tab stop width.
        fold: If True, words longer than `width` will be folded onto a new line.
        precomputed_tab_sections: The output of `get_tab_widths` can be passed here directly,
            to prevent us from having to recompute the value.

    Returns:
        A list of indices to break the line at.
    """
    tab_size = min(tab_size, width)
    if precomputed_tab_sections:
        tab_sections = precomputed_tab_sections
    else:
        tab_sections = get_tab_widths(text, tab_size)

    break_positions: list[int] = []  # offsets to insert the breaks at
    append = break_positions.append
    cell_offset = 0
    _cell_len = cell_len

    tab_section_index = 0
    cumulative_widths = _cumulative_tab_widths(tab_sections)

    for chunk_start, end, chunk in chunks(text):
        start = chunk_start
        chunk_width = _cell_len(chunk)  # this cell len excludes tabs completely
        tab_width_before_start = cumulative_widths[start]
        tab_width_before_end = cumulative_widths[end]
        chunk_tab_width = tab_width_before_end - tab_width_before_start
        chunk_width += chunk_tab_width
        remaining_space = width - cell_offset
        chunk_fits = remaining_space >= chunk_width

        if chunk_fits:
            # Simplest case - the word fits within the remaining width for this line.
            cell_offset += chunk_width
        else:
            # Not enough space remaining for this word on the current line.
            if chunk_width > width:
                # The word doesn't fit on any line, so we must fold it
                if fold:
                    folded_word, tab_section_index = _fold_chunk(chunk, width, tab_sections, tab_section_index)
                    for last, line in _loop_last(folded_word):
                        if start:
                            append(start)
                        if last:
                            # Since cell_len ignores tabs, we need to check the width
                            # of the tabs in this line. The width of tabs within the
                            # line is computed by taking the difference between the
                            # cumulative width of tabs up to the end of the line and the
                            # cumulative width of tabs up to the start of the line.
                            line_tab_widths = cumulative_widths[start + len(line)] - cumulative_widths[start]
                            cell_offset = _cell_len(line) + line_tab_widths
                        else:
                            start += len(line)
                else:
                    # Folding isn't allowed, so crop the word.
                    if start:
                        append(start)
                    cell_offset = chunk_width
            elif cell_offset and start:
                # The word doesn't fit within the remaining space on the current
                # line, but it *can* fit on to the next (empty) line.
                append(start)
                cell_offset = chunk_width

    return break_positions
