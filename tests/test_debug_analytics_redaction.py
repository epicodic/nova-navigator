"""Crash report redaction of secret frame locals (REQ-30, ADR-8)."""

from __future__ import annotations

import importlib
import io
import subprocess
import sys
import types

import pytest
from rich.console import Console

from nova_navigator import debug_analytics
from nova_navigator.debug_analytics import (
    REDACTED,
    SECRET_NAME_PARTS,
    SECRET_TYPE_NAMES,
    format_local,
    is_secret_local,
)
from nova_navigator.dialogs.credentials_dialog import Credentials
from tests._utils.known_hosts_files import make_key

TYPED_TEXT = "hunter2zq9"
INNOCENT = "innocent"


class _SubCredentials(Credentials):
    pass


class _ExplodingRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr must not be called")


class _CountingRepr:
    def __init__(self) -> None:
        self.calls = 0

    def __repr__(self) -> str:
        self.calls += 1
        return "counted"


def test_secret_name_parts_are_the_agreed_list() -> None:
    assert SECRET_NAME_PARTS == (
        "password",
        "passwd",
        "passphrase",
        "secret",
        "token",
        "credential",
        "private_key",
        "pkey",
        "api_key",
        "auth",
    )


@pytest.mark.parametrize("part", SECRET_NAME_PARTS)
def test_name_containing_part_is_redacted(part: str) -> None:
    name = f"x_{part}_y"
    assert is_secret_local(name, TYPED_TEXT)
    assert format_local(name, TYPED_TEXT) == REDACTED


@pytest.mark.parametrize("part", SECRET_NAME_PARTS)
def test_name_match_is_case_insensitive(part: str) -> None:
    assert format_local(part.upper(), TYPED_TEXT) == REDACTED


@pytest.mark.parametrize("part", SECRET_NAME_PARTS)
def test_bare_part_as_name_is_redacted(part: str) -> None:
    assert format_local(part, TYPED_TEXT) == REDACTED


def test_credentials_instance_redacted_under_innocent_name() -> None:
    value = Credentials("someone", TYPED_TEXT)
    assert is_secret_local(INNOCENT, value)
    assert format_local(INNOCENT, value) == REDACTED


def test_credentials_subclass_redacted() -> None:
    assert format_local(INNOCENT, _SubCredentials("someone", TYPED_TEXT)) == REDACTED


@pytest.mark.parametrize("kind", ["ecdsa", "rsa"])
def test_pkey_instance_redacted_under_innocent_name(kind: str) -> None:
    assert format_local(INNOCENT, make_key(kind)) == REDACTED


def test_secret_type_names_resolve_to_real_classes() -> None:
    for dotted in SECRET_TYPE_NAMES:
        module_name, _, class_name = dotted.rpartition(".")
        cls = getattr(importlib.import_module(module_name), class_name)
        assert isinstance(cls, type)
        assert f"{cls.__module__}.{cls.__qualname__}" == dotted
    assert len(SECRET_TYPE_NAMES) == 2


@pytest.mark.parametrize(
    ("name", "value"),
    [("count", 3), ("path", "some/path"), ("items", [1, "a"])],
)
def test_other_locals_keep_their_repr(name: str, value: object) -> None:
    assert not is_secret_local(name, value)
    assert format_local(name, value) == repr(value)


def test_container_contents_are_not_inspected() -> None:
    value = {"k": Credentials("someone", TYPED_TEXT)}
    assert format_local(INNOCENT, value) == repr(value)


def test_unrepresentable_value_reports_repr_failed() -> None:
    assert format_local(INNOCENT, _ExplodingRepr()) == "<repr failed>"


def test_redacted_value_repr_is_never_called() -> None:
    counted = _CountingRepr()
    assert format_local("password", counted) == REDACTED
    assert counted.calls == 0
    assert format_local("password", _ExplodingRepr()) == REDACTED


def _frame_with_locals() -> types.FrameType:
    count = 3
    password = TYPED_TEXT
    holder = Credentials("someone", TYPED_TEXT)
    assert count == 3
    assert holder.password == password
    return sys._getframe()


def test_render_frame_locals_writes_marker_and_other_locals() -> None:
    frame = _frame_with_locals()
    buffer = io.StringIO()
    debug_analytics._render_frame_locals(Console(file=buffer, width=200), frame)
    text = buffer.getvalue()
    assert TYPED_TEXT not in text
    assert REDACTED in text
    assert "count" in text
    assert "= 3" in text


def test_importing_debug_analytics_does_not_load_dialogs_or_paramiko() -> None:
    code = "import sys, nova_navigator.debug_analytics\nbad = [m for m in sys.modules if m == 'paramiko' or m.startswith('nova_navigator.dialogs')]\nprint(bad)\nsys.exit(1 if bad else 0)\n"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
