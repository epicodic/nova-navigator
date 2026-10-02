"""The vendored wrap helpers and tree-sitter loader behave like the installed Textual's (ACT7 design 7).

Skipped when upstream removes a module.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.document import _wrap as vendored_wrap
from nova_editor.widget import _tree_sitter as vendored_tree_sitter

upstream_wrap = pytest.importorskip("textual._wrap")
upstream_cells = pytest.importorskip("textual._cells")
upstream_tree_sitter = pytest.importorskip("textual._tree_sitter")

ALPHABET = "ab cd\tef-日本語é́\U0001f600\u200b"
EXAMPLES = 2000


@settings(derandomize=True, max_examples=EXAMPLES, deadline=None)
@given(
    text=st.text(alphabet=ALPHABET, max_size=150),
    width=st.integers(1, 40),
    tab=st.integers(1, 8),
    fold=st.booleans(),
)
def test_compute_wrap_offsets_matches_upstream(text: str, width: int, tab: int, fold: bool) -> None:
    assert vendored_wrap.compute_wrap_offsets(text, width, tab, fold) == upstream_wrap.compute_wrap_offsets(text, width, tab, fold)


@settings(derandomize=True, max_examples=EXAMPLES, deadline=None)
@given(text=st.text(alphabet=ALPHABET, max_size=80), cells=st.integers(0, 120), tab=st.integers(1, 8))
def test_cell_width_to_column_index_matches_upstream(text: str, cells: int, tab: int) -> None:
    assert vendored_wrap.cell_width_to_column_index(text, cells, tab) == upstream_cells.cell_width_to_column_index(text, cells, tab)


@pytest.mark.parametrize("name", ["python", "json", "css", "xml", "no-such-language"])
def test_get_language_agrees_with_upstream(name: str) -> None:
    assert vendored_tree_sitter.TREE_SITTER == upstream_tree_sitter.TREE_SITTER
    assert (vendored_tree_sitter.get_language(name) is None) == (upstream_tree_sitter.get_language(name) is None)
