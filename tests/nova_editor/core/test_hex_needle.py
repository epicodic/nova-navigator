"""Unit tests of `parse_hex_needle`: the hex text of the Find popup to bytes."""

from __future__ import annotations

import pytest

from nova_editor.core.search import HexNeedleError, parse_hex_needle

EMPTY_MESSAGE = "Enter hex bytes, for example DE AD BE EF"


def test_pairs_with_spaces() -> None:
    assert parse_hex_needle("DE AD BE EF") == b"\xde\xad\xbe\xef"


def test_no_spaces_and_mixed_case() -> None:
    assert parse_hex_needle("deadBEEF") == b"\xde\xad\xbe\xef"
    assert parse_hex_needle("dEaD") == b"\xde\xad"


def test_whitespace_anywhere() -> None:
    assert parse_hex_needle("  D E\tA\nD  ") == b"\xde\xad"
    assert parse_hex_needle("DEA D") == b"\xde\xad"


def test_odd_digits_message_names_the_count() -> None:
    with pytest.raises(HexNeedleError) as caught:
        parse_hex_needle("ABC")
    assert str(caught.value) == "Odd number of hex digits (3)"


def test_non_hex_character_message() -> None:
    with pytest.raises(HexNeedleError) as caught:
        parse_hex_needle("DE ZZ")
    assert str(caught.value) == "Not a hex digit: 'Z'"


def test_non_hex_wins_over_parity() -> None:
    with pytest.raises(HexNeedleError) as caught:
        parse_hex_needle("G")
    assert str(caught.value) == "Not a hex digit: 'G'"


@pytest.mark.parametrize("text", ["0xDE", "DE,AD"])
def test_prefix_and_comma_are_rejected(text: str) -> None:
    with pytest.raises(HexNeedleError) as caught:
        parse_hex_needle(text)
    assert str(caught.value).startswith("Not a hex digit: ")


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_empty_and_blank(text: str) -> None:
    with pytest.raises(HexNeedleError) as caught:
        parse_hex_needle(text)
    assert str(caught.value) == EMPTY_MESSAGE


def test_the_error_is_a_value_error() -> None:
    assert issubclass(HexNeedleError, ValueError)
