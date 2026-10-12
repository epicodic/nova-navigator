"""The pure hex input rules (REQ-9, REQ-15, REQ-16): nibble state machine and clipboard text."""

from __future__ import annotations

import pytest

from nova_editor.widget.hex_input import (
    DigitPlan,
    HexTextError,
    char_text,
    format_hex_pairs,
    parse_hex_text,
    plan_digit,
)

# --- plan_digit -------------------------------------------------------------------------------------------------


def test_append_at_the_end_position_in_overwrite_mode() -> None:
    plan = plan_digit(0xA, 4, 0, 4, False, None)
    assert plan == DigitPlan(start=4, end=4, data=b"\xa0", cursor_offset=4, cursor_nibble=1, completes=False)


def test_append_at_the_end_position_in_insert_mode() -> None:
    plan = plan_digit(0xA, 4, 0, 4, True, None)
    assert plan == DigitPlan(start=4, end=4, data=b"\xa0", cursor_offset=4, cursor_nibble=1, completes=False)


def test_append_into_an_empty_document() -> None:
    plan = plan_digit(0x1, 0, 0, 0, False, None)
    assert (plan.start, plan.end, plan.data, plan.cursor_offset, plan.cursor_nibble) == (0, 0, b"\x10", 0, 1)


def test_overwrite_high_digit_keeps_the_low_nibble() -> None:
    plan = plan_digit(0xA, 1, 0, 4, False, 0x5C)
    assert plan == DigitPlan(start=1, end=2, data=b"\xac", cursor_offset=1, cursor_nibble=1, completes=False)


def test_overwrite_low_digit_keeps_the_high_nibble_and_moves_to_the_next_byte() -> None:
    plan = plan_digit(0x3, 1, 1, 4, False, 0x5C)
    assert plan == DigitPlan(start=1, end=2, data=b"\x53", cursor_offset=2, cursor_nibble=0, completes=False)


def test_overwrite_low_digit_of_the_last_byte_lands_on_the_end_position() -> None:
    plan = plan_digit(0x3, 3, 1, 4, False, 0x5C)
    assert (plan.start, plan.end, plan.data, plan.cursor_offset, plan.cursor_nibble) == (3, 4, b"\x53", 4, 0)


def test_insert_high_digit_inserts_x0_before_the_byte() -> None:
    plan = plan_digit(0x7, 1, 0, 4, True, 0x5C)
    assert plan == DigitPlan(start=1, end=1, data=b"\x70", cursor_offset=1, cursor_nibble=1, completes=False)


def test_insert_low_digit_replaces_the_low_nibble_of_the_byte() -> None:
    plan = plan_digit(0x3, 1, 1, 4, True, 0x70)
    assert plan == DigitPlan(start=1, end=2, data=b"\x73", cursor_offset=2, cursor_nibble=0, completes=False)


def test_low_digit_completes_only_when_the_byte_was_made_by_the_previous_high_digit() -> None:
    assert plan_digit(0x3, 1, 1, 4, True, 0x70, half=True).completes is True
    assert plan_digit(0x3, 1, 1, 4, False, 0x70, half=True).completes is True
    assert plan_digit(0x3, 1, 1, 4, True, 0x70, half=False).completes is False


def test_half_flag_never_completes_a_high_digit() -> None:
    assert plan_digit(0x3, 1, 0, 4, True, 0x70, half=True).completes is False
    assert plan_digit(0x3, 4, 0, 4, False, None, half=True).completes is False


def test_the_low_digit_after_an_append_is_row_four() -> None:
    first = plan_digit(0xA, 4, 0, 4, False, None)
    second = plan_digit(0xB, first.cursor_offset, first.cursor_nibble, 5, False, 0xA0, half=True)
    assert (second.start, second.end, second.data) == (4, 5, b"\xab")
    assert (second.cursor_offset, second.cursor_nibble) == (5, 0)
    assert second.completes is True


@pytest.mark.parametrize("digit", range(16))
@pytest.mark.parametrize("insert", [False, True])
def test_every_digit_lands_in_its_nibble(digit: int, insert: bool) -> None:
    high = plan_digit(digit, 0, 0, 2, insert, 0xFF)
    low = plan_digit(digit, 0, 1, 2, insert, 0xFF)
    assert high.data[0] >> 4 == digit
    assert low.data[0] & 0x0F == digit
    assert low.data[0] >> 4 == 0xF


def test_a_current_byte_is_required_inside_the_document() -> None:
    with pytest.raises(ValueError, match="current byte is required"):
        plan_digit(1, 0, 1, 4, False, None)


@pytest.mark.parametrize("digit", [-1, 16])
def test_digit_out_of_range_is_rejected(digit: int) -> None:
    with pytest.raises(ValueError, match="out of range"):
        plan_digit(digit, 0, 0, 4, False, 0)


# --- format_hex_pairs and char_text --------------------------------------------------------------------------------


def test_format_hex_pairs_is_upper_case_pairs_separated_by_single_spaces() -> None:
    assert format_hex_pairs(b"\xde\xad\xbe\xef") == "DE AD BE EF"
    assert format_hex_pairs(b"\x00") == "00"
    assert format_hex_pairs(b"") == ""


def test_char_text_shows_printable_ascii_and_dots() -> None:
    assert char_text(b"A\x00B") == "A.B"
    assert char_text(b"\x1f\x20\x7e\x7f\x80\xff") == ". ~..."
    assert char_text(b"") == ""


# --- parse_hex_text -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("de ad be ef", b"\xde\xad\xbe\xef"),
        ("DEADBEEF", b"\xde\xad\xbe\xef"),
        ("0xDE 0xAD", b"\xde\xad"),
        ("0Xde 0xaD", b"\xde\xad"),
        ("de\tad\nbe\r\nef", b"\xde\xad\xbe\xef"),
        ("de adbe ef", b"\xde\xad\xbe\xef"),
        ("d e", b"\xde"),
        ("  00  ", b"\x00"),
    ],
)
def test_accepted_forms(text: str, expected: bytes) -> None:
    assert parse_hex_text(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_empty_text_is_an_error(text: str) -> None:
    with pytest.raises(HexTextError) as info:
        parse_hex_text(text)
    assert str(info.value) == "Nothing to paste: the clipboard is empty."


def test_non_hex_character_names_its_one_based_position() -> None:
    with pytest.raises(HexTextError) as info:
        parse_hex_text("de zz")
    assert str(info.value) == 'Cannot paste into the hex pane: "z" at position 4 of the clipboard text is not a hex digit.'


@pytest.mark.parametrize("text", ["de,ad", "de:ad", r"\xde", "de-ad"])
def test_other_separators_are_not_accepted(text: str) -> None:
    with pytest.raises(HexTextError, match="is not a hex digit"):
        parse_hex_text(text)


def test_control_character_is_shown_as_a_code_point() -> None:
    with pytest.raises(HexTextError) as info:
        parse_hex_text("de\x01")
    assert str(info.value) == ('Cannot paste into the hex pane: "U+0001" at position 3 of the clipboard text is not a hex digit.')


def test_prefix_inside_a_token_is_not_a_prefix() -> None:
    with pytest.raises(HexTextError) as info:
        parse_hex_text("d0x")
    assert '"x" at position 3' in str(info.value)


def test_odd_digit_count_is_an_error() -> None:
    with pytest.raises(HexTextError) as info:
        parse_hex_text("de a")
    assert str(info.value) == "Cannot paste into the hex pane: the clipboard text holds an odd number of hex digits (3)."


def test_prefix_without_digits_names_its_position() -> None:
    with pytest.raises(HexTextError) as info:
        parse_hex_text("0x")
    assert str(info.value) == 'Cannot paste into the hex pane: "0x" without digits at position 1.'
    with pytest.raises(HexTextError) as info:
        parse_hex_text("de 0X")
    assert str(info.value) == 'Cannot paste into the hex pane: "0x" without digits at position 4.'


def test_the_first_problem_in_the_text_wins() -> None:
    with pytest.raises(HexTextError, match="position 2"):
        parse_hex_text("dg 0x")
