"""The ASCII control character name table (S0003 ACT9, REQ-26)."""

from __future__ import annotations

from typing import cast

import pytest

from nova_editor.core.control_names import CONTROL_NAMES, bracketed_control_name, control_name

NAMES = [
    "NUL",
    "SOH",
    "STX",
    "ETX",
    "EOT",
    "ENQ",
    "ACK",
    "BEL",
    "BS",
    "HT",
    "LF",
    "VT",
    "FF",
    "CR",
    "SO",
    "SI",
    "DLE",
    "DC1",
    "DC2",
    "DC3",
    "DC4",
    "NAK",
    "SYN",
    "ETB",
    "CAN",
    "EM",
    "SUB",
    "ESC",
    "FS",
    "GS",
    "RS",
    "US",
]


@pytest.mark.parametrize("byte", range(32))
def test_every_c0_control_character_has_its_name(byte: int) -> None:
    assert control_name(byte) == NAMES[byte]
    assert bracketed_control_name(byte) == f"<{NAMES[byte]}>"


def test_del_has_a_name() -> None:
    assert control_name(0x7F) == "DEL"
    assert bracketed_control_name(0x7F) == "<DEL>"


def test_the_table_has_exactly_33_entries() -> None:
    assert len(CONTROL_NAMES) == 33
    assert sorted(CONTROL_NAMES) == [*range(32), 0x7F]


@pytest.mark.parametrize("byte", [0x20, 0x41, 0x7E, 0x80, 0x9B, 0xA0, 0xFF, -1, 256])
def test_other_values_have_no_name(byte: int) -> None:
    assert control_name(byte) is None
    assert bracketed_control_name(byte) is None


@pytest.mark.parametrize(("byte", "text"), [(0x00, "<NUL>"), (0x07, "<BEL>"), (0x0A, "<LF>"), (0x0D, "<CR>"), (0x1B, "<ESC>")])
def test_the_owner_examples(byte: int, text: str) -> None:
    assert bracketed_control_name(byte) == text


def test_the_table_is_read_only() -> None:
    table = cast("dict[int, str]", CONTROL_NAMES)
    with pytest.raises(TypeError):
        table[0] = "X"
    with pytest.raises(TypeError):
        del table[0]
    for method in ("pop", "popitem", "update", "clear", "setdefault"):
        assert getattr(table, method, None) is None
    assert control_name(0) == "NUL"
    assert len(CONTROL_NAMES) == 33
