"""Tests for shell editor protocol payload parsing and nonce generation."""

from __future__ import annotations

from nova_navigator.terminal.shell_editor_protocol import (
    EditorOperation,
    EditorResponse,
    create_editor_nonce,
    parse_editor_response,
)


def test_parse_editor_response_accepts_ready_payload() -> None:
    response = parse_editor_response("nn;abc123;ready;1;0;0")
    assert response == EditorResponse(
        nonce="abc123",
        operation=EditorOperation.READY,
        sequence=1,
        buffer_length=0,
        cursor=0,
    )


def test_parse_editor_response_accepts_probe_payload() -> None:
    response = parse_editor_response("nn;nonce;probe;12;8;3")
    assert response == EditorResponse(
        nonce="nonce",
        operation=EditorOperation.PROBE,
        sequence=12,
        buffer_length=8,
        cursor=3,
    )


def test_parse_editor_response_accepts_stash_payload() -> None:
    response = parse_editor_response("nn;nonce;stash;22;15;6")
    assert response == EditorResponse(
        nonce="nonce",
        operation=EditorOperation.STASH,
        sequence=22,
        buffer_length=15,
        cursor=6,
    )


def test_parse_editor_response_accepts_restore_payload() -> None:
    response = parse_editor_response("nn;nonce;restore;33;15;6")
    assert response == EditorResponse(
        nonce="nonce",
        operation=EditorOperation.RESTORE,
        sequence=33,
        buffer_length=15,
        cursor=6,
    )


def test_parse_editor_response_accepts_cursor_zero_with_nonempty_buffer() -> None:
    response = parse_editor_response("nn;nonce;probe;5;9;0")
    assert response == EditorResponse(
        nonce="nonce",
        operation=EditorOperation.PROBE,
        sequence=5,
        buffer_length=9,
        cursor=0,
    )


def test_create_editor_nonce_has_expected_shape_and_is_unique() -> None:
    nonce_a = create_editor_nonce()
    nonce_b = create_editor_nonce()

    assert len(nonce_a) == 32
    assert len(nonce_b) == 32
    assert nonce_a != nonce_b

    int(nonce_a, 16)
    int(nonce_b, 16)


def test_parse_editor_response_rejects_malformed_numbers() -> None:
    assert parse_editor_response("nn;nonce;probe;NaN;1;1") is None
    assert parse_editor_response("nn;nonce;probe;1;x;1") is None
    assert parse_editor_response("nn;nonce;probe;1;1;x") is None


def test_parse_editor_response_rejects_unsupported_operation() -> None:
    assert parse_editor_response("nn;nonce;unknown;1;0;0") is None


def test_parse_editor_response_rejects_wrong_fields_and_prefix() -> None:
    assert parse_editor_response("xx;nonce;probe;1;0;0") is None
    assert parse_editor_response("nn;nonce;probe;1;0") is None
    assert parse_editor_response("nn;nonce;probe;1;0;0;extra") is None


def test_parse_editor_response_rejects_negative_values() -> None:
    assert parse_editor_response("nn;nonce;probe;-1;0;0") is None
    assert parse_editor_response("nn;nonce;probe;1;-1;0") is None
    assert parse_editor_response("nn;nonce;probe;1;0;-1") is None


def test_parse_editor_response_rejects_cursor_beyond_buffer() -> None:
    assert parse_editor_response("nn;nonce;probe;1;4;5") is None
