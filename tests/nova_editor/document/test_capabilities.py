"""Tests for DocumentBase row-access capability methods."""

from nova_editor.document._document import Document


def test_stock_document_capabilities() -> None:
    doc = Document("héllo\tworld\nx")
    assert doc.is_long(0) is False
    assert doc.line_length(0) == len("héllo\tworld")
    # row_byte_length: UTF-8 encoding with surrogateescape
    # "héllo\tworld" = h(1) + é(2) + l(1) + l(1) + o(1) + \t(1) + w(1) + o(1) + r(1) + l(1) + d(1) = 12 bytes
    assert doc.row_byte_length(0) == len("héllo\tworld".encode())
    assert doc.column_slice(0, 1, 4) == "éll"
    assert doc.has_char_at(0, 0) is True
    assert doc.has_char_at(0, len("héllo\tworld")) is False
    # display_column(0, 6): characters [0:6] = "héllo\t"
    # h(1 cell) + é(1 cell) + l(1 cell) + l(1 cell) + o(1 cell) = 5 cells
    # Tab from display col 5 advances to next tab stop: (5 // 4 + 1) * 4 = 8
    assert doc.display_column(0, 6) == 8
    # column_at_display(0, 8): which column covers display column 8?
    # Display columns: h(0-1) é(1-2) l(2-3) l(3-4) o(4-5) TAB(5-8)
    # Column 6 (the tab) covers display 8 at its end
    assert doc.column_at_display(0, 8) == 6
    # byte_offset(0, 2): characters [0:2] = "hé"
    # h(1 byte) + é(2 bytes) = 3 bytes
    assert doc.byte_offset(0, 2) == 3
    # byte_offset(1, 1): row 0 has 12 bytes + 1 newline = 13 bytes before row 1
    # Column 1 in row 1 ("x"): x(1 byte) = 1 byte after row start
    # Total: 13 + 1 = 14 bytes
    assert doc.byte_offset(1, 1) == len("héllo\tworld".encode()) + 1 + 1
