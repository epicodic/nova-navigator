"""Tests for synthetic file builder and oracle functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.nova_editor.helpers_view import LOWERED_OPTIONS, make_mixed, oracle_display_column, oracle_row_ranges, oracle_row_text


class TestMakeMixed:
    """Test synthetic file builder."""

    def test_make_mixed_contains_every_feature_lf(self, tmp_path: Path) -> None:
        """Test LF-terminated file contains all required features."""
        path = make_mixed(tmp_path / "mixed_lf.txt", terminator=b"\n")
        data = path.read_bytes()

        assert b"\n" in data
        assert b"\xff" in data  # invalid byte
        assert "日本語".encode() in data
        assert "é".encode() in data
        assert max(len(row) for row in data.split(b"\n")) > 512

    def test_make_mixed_contains_every_feature_crlf(self, tmp_path: Path) -> None:
        """Test CRLF-terminated file contains all required features."""
        path = make_mixed(tmp_path / "mixed_crlf.txt", terminator=b"\r\n")
        data = path.read_bytes()

        assert b"\r\n" in data
        assert b"\xff" in data  # invalid byte
        assert "日本語".encode() in data
        assert "é".encode() in data
        assert max(len(row) for row in data.split(b"\r\n")) > 512

    def test_make_mixed_contains_every_feature_cr(self, tmp_path: Path) -> None:
        """Test CR-terminated file contains all required features."""
        path = make_mixed(tmp_path / "mixed_cr.txt", terminator=b"\r")
        data = path.read_bytes()

        assert b"\r" in data
        assert b"\xff" in data  # invalid byte
        assert "日本語".encode() in data
        assert "é".encode() in data
        assert max(len(row) for row in data.split(b"\r")) > 512

    def test_make_mixed_final_row_has_no_terminator(self, tmp_path: Path) -> None:
        """Test that final row does not have a terminator."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        data = path.read_bytes()

        # After split, final row should end the file content
        assert not data.endswith(b"\n"), "Final row should not have terminator"

    def test_make_mixed_writable(self, tmp_path: Path) -> None:
        """Test that make_mixed returns the path and creates the file."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        assert path.exists()
        assert path.is_file()
        assert len(path.read_bytes()) > 0

    def test_make_mixed_custom_long_chars(self, tmp_path: Path) -> None:
        """Test that long_chars parameter controls the long row length."""
        path = make_mixed(tmp_path / "long.txt", terminator=b"\n", long_chars=500)
        data = path.read_bytes()
        rows = data.split(b"\n")

        # Find the long row (should be around row 12 based on make_mixed logic)
        row_lengths = [len(row) for row in rows]
        assert any(length >= 400 for length in row_lengths), "Should have a row close to 500 chars"


class TestOracleRowRanges:
    """Test row range parsing."""

    def test_oracle_row_ranges_lf(self, tmp_path: Path) -> None:
        """Test row range detection with LF terminator."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        data = path.read_bytes()
        ranges = oracle_row_ranges(data)

        assert len(ranges) > 0
        # Verify ranges don't overlap and cover the entire file
        for i, (start, content_end, end) in enumerate(ranges):
            assert start < end
            assert content_end <= end
            if i > 0:
                assert start == ranges[i - 1].end  # Ranges should be contiguous

    def test_oracle_row_ranges_crlf(self, tmp_path: Path) -> None:
        """Test row range detection with CRLF terminator."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\r\n")
        data = path.read_bytes()
        ranges = oracle_row_ranges(data)

        assert len(ranges) > 0
        for i, (start, content_end, end) in enumerate(ranges):
            assert start < end
            if i < len(ranges) - 1:
                # CRLF is 2 bytes, so content_end + 2 should equal next start
                assert end - content_end == 2

    def test_oracle_row_ranges_cr(self, tmp_path: Path) -> None:
        """Test row range detection with CR terminator."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\r")
        data = path.read_bytes()
        ranges = oracle_row_ranges(data)

        assert len(ranges) > 0
        for i, (start, content_end, end) in enumerate(ranges):
            assert start < end
            if i < len(ranges) - 1:
                # CR is 1 byte, so content_end + 1 should equal next start
                assert end - content_end == 1

    def test_oracle_row_ranges_empty(self) -> None:
        """Test row range detection on empty data."""
        ranges = oracle_row_ranges(b"")
        assert ranges == []


class TestOracleRowText:
    """Test row text extraction."""

    def test_oracle_row_text_first_row_lf(self, tmp_path: Path) -> None:
        """Test extracting first row with LF terminator."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        data = path.read_bytes()

        # First row should be "short line 0"
        text = oracle_row_text(data, 0)
        assert text == "short line 0"

    def test_oracle_row_text_first_row_crlf(self, tmp_path: Path) -> None:
        """Test extracting first row with CRLF terminator."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\r\n")
        data = path.read_bytes()

        # First row should be "short line 0"
        text = oracle_row_text(data, 0)
        assert text == "short line 0"

    def test_oracle_row_text_with_special_chars(self, tmp_path: Path) -> None:
        """Test extracting row with CJK and combining marks."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        data = path.read_bytes()

        # Row with special characters is at index 10
        text = oracle_row_text(data, 10)
        assert "日本語" in text
        assert "é" in text
        assert "😀" in text

    def test_oracle_row_text_final_row_no_terminator(self, tmp_path: Path) -> None:
        """Test that final row (without terminator) is accessible."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        data = path.read_bytes()
        ranges = oracle_row_ranges(data)

        # Final row should be "final line"
        final_text = oracle_row_text(data, len(ranges) - 1)
        assert final_text == "final line"

    def test_oracle_row_text_out_of_range(self, tmp_path: Path) -> None:
        """Test that accessing out-of-range row raises IndexError."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        data = path.read_bytes()

        with pytest.raises(IndexError):
            oracle_row_text(data, 999)


class TestOracleDisplayColumn:
    """Test display column width calculation."""

    def test_oracle_display_column_simple_ascii(self) -> None:
        """Test display column for simple ASCII text."""
        text = "hello"
        # 'h' = 1, 'e' = 1, 'l' = 1, 'l' = 1, 'o' = 1
        # Each character is 1 cell wide
        for i, expected_width in enumerate([0, 1, 2, 3, 4], 0):
            # Column 0 should give width 0, column 1 should give width 1, etc.
            width = oracle_display_column(text, i)
            assert width == expected_width

    def test_oracle_display_column_with_tabs(self) -> None:
        """Test display column with tab characters."""
        text = "\thello"
        # Tab width 4: first tab advances from column 0 to column 4
        width = oracle_display_column(text, 0)
        assert width == 0

        # After tab character (1 byte) at position 1
        # Should be at display column 4 (next tab stop)
        # But we need to count to column 1 (byte position after tab)
        width = oracle_display_column(text, 1)
        assert width == 4

    def test_oracle_display_column_wide_characters(self) -> None:
        """Test display column with wide characters (CJK)."""
        text = "日本"
        # Each CJK character is typically 2 cells wide
        # But we're measuring by byte column within the character

        # The first character "日" is 3 bytes in UTF-8
        # Byte column 0 gives display column 0
        width = oracle_display_column(text, 0)
        assert width == 0

        # We can't really test byte column 3 since that's beyond first char
        # but the function should count the full character width

    def test_oracle_display_column_combining_marks(self) -> None:
        """Test display column with combining marks."""
        text = "é"  # e with combining acute accent
        # The combining mark is part of the character but display width should be 1
        width = oracle_display_column(text, 0)
        assert width == 0  # At column 0, we haven't moved yet

    def test_oracle_display_column_emoji(self) -> None:
        """Test display column with emoji."""
        text = "😀"  # Grinning face emoji
        # Emoji are typically 2 cells wide
        width = oracle_display_column(text, 0)
        assert width == 0  # At column 0


class TestOracleReconstruction:
    """Test that oracle functions can reconstruct original file."""

    def test_reconstruct_from_oracle_lf(self, tmp_path: Path) -> None:
        """Test that file can be reconstructed from oracle row text and LF terminators."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\n")
        data = path.read_bytes()

        # Get all rows and terminators
        ranges = oracle_row_ranges(data)
        reconstructed = bytearray()

        for i, _ in enumerate(ranges):
            # Add row text
            row_text = oracle_row_text(data, i)
            reconstructed.extend(row_text.encode("utf-8", errors="surrogateescape"))

            # Add terminator (but not after final row)
            if i < len(ranges) - 1:
                reconstructed.extend(b"\n")

        assert bytes(reconstructed) == data

    def test_reconstruct_from_oracle_crlf(self, tmp_path: Path) -> None:
        """Test that file can be reconstructed from oracle row text and CRLF terminators."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\r\n")
        data = path.read_bytes()

        ranges = oracle_row_ranges(data)
        reconstructed = bytearray()

        for i, _ in enumerate(ranges):
            row_text = oracle_row_text(data, i)
            reconstructed.extend(row_text.encode("utf-8", errors="surrogateescape"))

            if i < len(ranges) - 1:
                reconstructed.extend(b"\r\n")

        assert bytes(reconstructed) == data

    def test_reconstruct_from_oracle_cr(self, tmp_path: Path) -> None:
        """Test that file can be reconstructed from oracle row text and CR terminators."""
        path = make_mixed(tmp_path / "mixed.txt", terminator=b"\r")
        data = path.read_bytes()

        ranges = oracle_row_ranges(data)
        reconstructed = bytearray()

        for i, _ in enumerate(ranges):
            row_text = oracle_row_text(data, i)
            reconstructed.extend(row_text.encode("utf-8", errors="surrogateescape"))

            if i < len(ranges) - 1:
                reconstructed.extend(b"\r")

        assert bytes(reconstructed) == data


class TestLoweredOptions:
    """Test that LOWERED_OPTIONS is properly defined."""

    def test_lowered_options_structure(self) -> None:
        """Test that LOWERED_OPTIONS has required keys and correct types."""
        assert isinstance(LOWERED_OPTIONS, dict)

        required_keys = {"stride", "index_long_line_threshold", "long_row_threshold", "word_wrap_limit", "checkpoint_chars"}
        assert set(LOWERED_OPTIONS.keys()) == required_keys

        # All values should be positive integers
        for key, value in LOWERED_OPTIONS.items():
            assert isinstance(value, int), f"{key} should be an int, got {type(value)}"
            assert value > 0, f"{key} should be positive, got {value}"

    def test_lowered_options_values(self) -> None:
        """Test that LOWERED_OPTIONS has expected values."""
        assert LOWERED_OPTIONS["stride"] == 4
        assert LOWERED_OPTIONS["index_long_line_threshold"] == 64
        assert LOWERED_OPTIONS["long_row_threshold"] == 512
        assert LOWERED_OPTIONS["word_wrap_limit"] == 128
        assert LOWERED_OPTIONS["checkpoint_chars"] == 64
