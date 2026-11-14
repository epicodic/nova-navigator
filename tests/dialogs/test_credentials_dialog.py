"""``Credentials`` keeps the typed password out of its repr and str."""

from __future__ import annotations

from nova_navigator.dialogs.credentials_dialog import Credentials

TYPED_TEXT = "hunter2zq9"
OTHER_TEXT = "other8xk4"


def test_credentials_repr_hides_password() -> None:
    text = repr(Credentials("alice", TYPED_TEXT))
    assert TYPED_TEXT not in text
    assert "alice" in text


def test_credentials_str_hides_password() -> None:
    assert TYPED_TEXT not in str(Credentials("alice", TYPED_TEXT))


def test_credentials_equality_still_compares_password() -> None:
    assert Credentials("alice", TYPED_TEXT) == Credentials("alice", TYPED_TEXT)
    assert Credentials("alice", TYPED_TEXT) != Credentials("alice", OTHER_TEXT)


def test_credentials_positional_construction_still_works() -> None:
    creds = Credentials("alice", TYPED_TEXT)
    assert (creds.username, creds.password) == ("alice", TYPED_TEXT)
