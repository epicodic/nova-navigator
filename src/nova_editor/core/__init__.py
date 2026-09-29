"""Textual-free core of the editor: byte source, line index and long-line index.

No module in this package may import Textual (REQ-17); a test enforces it.
"""

from nova_editor.core.byte_source import ByteSource, PreadSource, SourceChanged
from nova_editor.core.line_index import LineIndex, LineSnapshot, RowRange
from nova_editor.core.long_line_index import Frontier, LongLineIndex

__all__ = [
    "ByteSource",
    "Frontier",
    "LineIndex",
    "LineSnapshot",
    "LongLineIndex",
    "PreadSource",
    "RowRange",
    "SourceChanged",
]
