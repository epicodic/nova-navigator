"""The secret registry replaces registered values and their repr forms (REQ-26, ADR-5)."""

from __future__ import annotations

import pytest

from nova_navigator.secret_registry import REDACTED, register_secret, register_uri_secrets, register_userinfo, scrub

TRICKY = "pa'ss\"wo\\rd\tä-[b]"


def _forms() -> list[str]:
    return [
        TRICKY,
        repr(TRICKY),
        repr(repr(TRICKY)),
        repr(TRICKY.encode()),
        ascii(TRICKY),
        repr({"k": TRICKY}),
        repr(f"both ' and \" {TRICKY}"),
        repr(b"\x00'\"" + TRICKY.encode()),
    ]


@pytest.mark.parametrize("text", _forms())
def test_every_form_is_replaced(text: str) -> None:
    register_secret(TRICKY)
    result = scrub(f"before {text} after")
    assert REDACTED in result
    assert "wo" not in result
    assert result.startswith("before ")
    assert result.endswith(" after")


def test_overlapping_secrets_leave_no_remainder() -> None:
    register_secret("abcdef")
    register_secret("defghi")
    assert scrub("x abcdefghi y") == f"x {REDACTED} y"


def test_contained_secret_is_covered_by_the_longer_one() -> None:
    register_secret("cd")
    register_secret("abcdef")
    assert scrub("abcdef") == REDACTED


@pytest.mark.parametrize("value", ["", None])
def test_empty_values_are_ignored(value: str | None) -> None:
    register_secret(value)
    assert scrub("text") == "text"


@pytest.mark.parametrize(
    "uri",
    ["ssh://user:zq9pw@host/p", "ssh://user:zq9pw@host:22", "ssh://h/a.tar/sftp://user:zq9pw@other/x"],
)
def test_uri_password_is_registered(uri: str) -> None:
    register_uri_secrets(uri)
    assert scrub("zq9pw") == REDACTED
    assert scrub("user") == "user"


def test_uri_password_containing_at_sign() -> None:
    register_uri_secrets("ssh://user:p@ss9@host/")
    assert scrub("p@ss9") == REDACTED


@pytest.mark.parametrize("uri", ["/local/path", "ssh://user@host/p", "ssh://host:22/p"])
def test_uri_without_password_registers_nothing(uri: str) -> None:
    register_uri_secrets(uri)
    assert scrub(uri) == uri


@pytest.mark.parametrize("userinfo", [None, "", "user", "user:"])
def test_userinfo_without_password_registers_nothing(userinfo: str | None) -> None:
    register_userinfo(userinfo)
    assert scrub("user text") == "user text"
