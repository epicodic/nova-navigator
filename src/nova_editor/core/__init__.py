"""Textual-free core of the editor: byte source, line index, long-line index and the piece tree.

No module in this package may import Textual (REQ-17); a test enforces it.
"""

from nova_editor.core.add_store import AddSegment, AddStore
from nova_editor.core.byte_source import ByteSource, ChangeKind, FileIdentity, PreadSource, SourceChanged
from nova_editor.core.line_index import LineIndex, LineSnapshot, RowRange
from nova_editor.core.long_line_index import Frontier, LongLineIndex
from nova_editor.core.memory_source import BytesSource
from nova_editor.core.original_source import OriginalSource, RowNotIndexed
from nova_editor.core.piece_table import PieceTable
from nova_editor.core.piece_tree import Location, PieceTree
from nova_editor.core.pieces import Aggregate, Content, Piece, PieceSource, combine, make_piece, merge_pieces
from nova_editor.core.rebase import RebasePlan, Rebaser
from nova_editor.core.row_scanner import RowScanner
from nova_editor.core.row_source import RowSource
from nova_editor.core.save import check_path
from nova_editor.core.save_layout import SaveLayout
from nova_editor.core.search import (
    CONTEXT_AFTER,
    CONTEXT_BEFORE,
    MAX_NEEDLE_BYTES,
    MAX_PATTERN_CHARS,
    Matcher,
    SearchCancelled,
    SearchError,
    SearchStale,
    compile_matcher,
)

__all__ = [
    "CONTEXT_AFTER",
    "CONTEXT_BEFORE",
    "MAX_NEEDLE_BYTES",
    "MAX_PATTERN_CHARS",
    "AddSegment",
    "AddStore",
    "Aggregate",
    "ByteSource",
    "BytesSource",
    "ChangeKind",
    "Content",
    "FileIdentity",
    "Frontier",
    "LineIndex",
    "LineSnapshot",
    "Location",
    "LongLineIndex",
    "Matcher",
    "OriginalSource",
    "Piece",
    "PieceSource",
    "PieceTable",
    "PieceTree",
    "PreadSource",
    "RebasePlan",
    "Rebaser",
    "RowNotIndexed",
    "RowRange",
    "RowScanner",
    "RowSource",
    "SaveLayout",
    "SearchCancelled",
    "SearchError",
    "SearchStale",
    "SourceChanged",
    "check_path",
    "combine",
    "compile_matcher",
    "make_piece",
    "merge_pieces",
]
