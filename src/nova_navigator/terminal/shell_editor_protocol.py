"""Shell editor protocol payload types and parser."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum

EDITOR_OSC_CODE = 777

PROBE_SEQUENCE = b"\x1b[99~"
STASH_SEQUENCE = b"\x1b[98~"
RESTORE_SEQUENCE = b"\x1b[97~"
_EXPECTED_FIELD_COUNT = 6


class EditorOperation(StrEnum):
    """Supported shell editor protocol operations."""

    READY = "ready"
    PROBE = "probe"
    STASH = "stash"
    RESTORE = "restore"


@dataclass(frozen=True)
class EditorResponse:
    """Parsed shell editor protocol response."""

    nonce: str
    operation: EditorOperation
    sequence: int
    buffer_length: int
    cursor: int


def create_editor_nonce() -> str:
    """Create a cryptographically strong protocol nonce."""
    return secrets.token_hex(16)


def parse_editor_response(payload: str) -> EditorResponse | None:
    """Parse an OSC 777 shell editor payload into a typed response."""
    fields = payload.split(";")
    if len(fields) != _EXPECTED_FIELD_COUNT or fields[0] != "nn":
        return None

    _, nonce, operation_raw, sequence_raw, buffer_length_raw, cursor_raw = fields

    try:
        operation = EditorOperation(operation_raw)
    except ValueError:
        return None

    try:
        sequence = int(sequence_raw)
        buffer_length = int(buffer_length_raw)
        cursor = int(cursor_raw)
    except ValueError:
        return None

    if sequence < 0 or buffer_length < 0 or cursor < 0:
        return None

    if cursor > buffer_length:
        return None

    return EditorResponse(
        nonce=nonce,
        operation=operation,
        sequence=sequence,
        buffer_length=buffer_length,
        cursor=cursor,
    )
