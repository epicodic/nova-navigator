"""Unit tests for choosing the shell of the local embedded terminal."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from nova_navigator.terminal.local_shell import ShellChoice, resolve_local_shell


def _executable(directory: Path, name: str = "shell") -> str:
    path = directory / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def _login_returning(path: str) -> Callable[[], str]:
    return lambda: path


def test_usable_shell_from_environment_is_returned(tmp_path: Path) -> None:
    shell = _executable(tmp_path)
    choice = resolve_local_shell({"SHELL": shell}, _login_returning("/bin/bash"))
    assert choice == ShellChoice(path=shell)


def test_bare_shell_name_is_resolved_in_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shell = _executable(tmp_path, "mysh")
    monkeypatch.setenv("PATH", str(tmp_path))
    choice = resolve_local_shell({"SHELL": "mysh"}, _login_returning("/bin/bash"))
    assert choice == ShellChoice(path=shell)


@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_shell_uses_login_shell_silently(tmp_path: Path, value: str | None) -> None:
    login = _executable(tmp_path, "login")
    env = {} if value is None else {"SHELL": value}
    choice = resolve_local_shell(env, _login_returning(login))
    assert choice == ShellChoice(path=login, rejected=None)


def test_missing_path_falls_back_to_login_shell(tmp_path: Path) -> None:
    login = _executable(tmp_path, "login")
    missing = str(tmp_path / "gone")
    choice = resolve_local_shell({"SHELL": missing}, _login_returning(login))
    assert choice == ShellChoice(path=login, rejected=missing)


def test_non_executable_file_falls_back_to_login_shell(tmp_path: Path) -> None:
    login = _executable(tmp_path, "login")
    plain = tmp_path / "plain"
    plain.write_text("text\n")
    plain.chmod(0o644)
    choice = resolve_local_shell({"SHELL": str(plain)}, _login_returning(login))
    assert choice == ShellChoice(path=login, rejected=str(plain))


def test_directory_falls_back_to_login_shell(tmp_path: Path) -> None:
    login = _executable(tmp_path, "login")
    choice = resolve_local_shell({"SHELL": str(tmp_path)}, _login_returning(login))
    assert choice == ShellChoice(path=login, rejected=str(tmp_path))


def test_unusable_shell_falls_back_to_login_shell(tmp_path: Path) -> None:
    choice = resolve_local_shell({"SHELL": str(tmp_path / "gone")}, lambda: "/bin/bash")
    assert choice == ShellChoice(path="/bin/bash", rejected=str(tmp_path / "gone"))


def test_unusable_login_shell_falls_back_to_bin_sh(tmp_path: Path) -> None:
    choice = resolve_local_shell({"SHELL": str(tmp_path / "gone")}, _login_returning(str(tmp_path / "none")))
    assert choice == ShellChoice(path="/bin/sh", rejected=str(tmp_path / "gone"))


def test_missing_login_entry_falls_back_to_bin_sh() -> None:
    def no_entry() -> str:
        raise KeyError("uid")

    assert resolve_local_shell({}, no_entry) == ShellChoice(path="/bin/sh", rejected=None)


def test_executable_non_shell_is_returned_unchanged() -> None:
    choice = resolve_local_shell({"SHELL": "/usr/sbin/nologin"}, _login_returning("/bin/bash"))
    assert choice == ShellChoice(path="/usr/sbin/nologin", rejected=None)
