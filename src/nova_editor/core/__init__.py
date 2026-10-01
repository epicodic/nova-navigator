"""Textual-free core of the editor: byte source, line index, long-line index and the piece tree.

No module in this package may import Textual (REQ-17); a test enforces it.
"""

from nova_editor.core.byte_source import ByteSource, PreadSource, SourceChanged
from nova_editor.core.line_index import LineIndex, LineSnapshot, RowRange
from nova_editor.core.long_line_index import Frontier, LongLineIndex
from nova_editor.core.piece_tree import Location, PieceTree
from nova_editor.core.pieces import Aggregate, Content, Piece, PieceSource, combine, make_piece, merge_pieces

__all__ = [
    "Aggregate",
    "ByteSource",
    "Content",
    "Frontier",
    "LineIndex",
    "LineSnapshot",
    "Location",
    "LongLineIndex",
    "Piece",
    "PieceSource",
    "PieceTree",
    "PreadSource",
    "RowRange",
    "SourceChanged",
    "combine",
    "make_piece",
    "merge_pieces",
]
