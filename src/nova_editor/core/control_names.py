"""ASCII control character names: the standard abbreviations for 0x00-0x1F and 0x7F (REQ-26)."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

DEL_BYTE = 0x7F
"""The byte value of DEL, the only control character outside 0x00-0x1F."""

_C0_NAMES = (
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
)

CONTROL_NAMES: Mapping[int, str] = MappingProxyType({**dict(enumerate(_C0_NAMES)), DEL_BYTE: "DEL"})
"""Read-only table from byte value to abbreviation: 33 entries, 0x00-0x1F and 0x7F; no other byte has a name."""


def control_name(byte: int) -> str | None:
    """Return the abbreviation of the control character ``byte`` (``"BEL"`` for 7), or None when it has no name.

    ``byte`` must be an int (a byte value).
    """
    return CONTROL_NAMES.get(byte)


def bracketed_control_name(byte: int) -> str | None:
    """Return the abbreviation in angle brackets (``"<BEL>"`` for 7), or None when ``byte`` has no name.

    ``byte`` must be an int (a byte value).
    """
    name = CONTROL_NAMES.get(byte)
    return None if name is None else f"<{name}>"
