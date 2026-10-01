"""Textual-free core of the editor: byte source, line index, long-line index and the piece tree.

No module in this package may import Textual (REQ-17); a test enforces it.
"""

from nova_editor.core.add_store import AddSegment, AddStore
from nova_editor.core.byte_source import ByteSource, PreadSource, SourceChanged
from nova_editor.core.line_index import LineIndex, LineSnapshot, RowRange
from nova_editor.core.long_line_index import Frontier, LongLineIndex
from nova_editor.core.memory_source import BytesSource
from nova_editor.core.original_source import OriginalSource, RowNotIndexed
from nova_editor.core.piece_table import PieceTable
from nova_editor.core.piece_tree import Location, PieceTree
from nova_editor.core.pieces import Aggregate, Content, Piece, PieceSource, combine, make_piece, merge_pieces

__all__ = [
    "AddSegment",
    "AddStore",
    "Aggregate",
    "ByteSource",
    "BytesSource",
    "Content",
    "Frontier",
    "LineIndex",
    "LineSnapshot",
    "Location",
    "LongLineIndex",
    "OriginalSource",
    "Piece",
    "PieceSource",
    "PieceTable",
    "PieceTree",
    "PreadSource",
    "RowNotIndexed",
    "RowRange",
    "SourceChanged",
    "combine",
    "make_piece",
    "merge_pieces",
]
