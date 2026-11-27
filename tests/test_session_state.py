"""Tests for the session state file and usable-directory check."""

import os
from pathlib import Path

import pytest

from nova_navigator import session_state
from nova_navigator.config import loader
from nova_navigator.session_state import (
    SORT_COLUMN_NAMES,
    PanelState,
    SessionState,
    load_session_state,
    save_session_state,
    usable_directory,
)
from nova_navigator.widgets.directory_browser import SortKey


def _write_state(text: str) -> None:
    path = session_state._STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_save_load_round_trip() -> None:
    state = SessionState(
        left=PanelState(sort_column="name", sort_ascending=True),
        right=PanelState(sort_column="size", sort_ascending=False),
        right_path="/home/user/projects",
    )

    save_session_state(state)

    assert load_session_state() == state


@pytest.mark.parametrize(
    "stored",
    ["ssh://h/x", "projects/relative", "/home/user/a\\u0000b"],
    ids=["uri", "relative", "nul"],
)
def test_uri_as_stored_path_is_ignored(stored: str) -> None:
    _write_state(f'[right]\nsort_column = "size"\npath = "{stored}"\n')

    assert load_session_state() == SessionState(right=PanelState(sort_column="size"))


@pytest.mark.parametrize(
    "content",
    [b'[left]\nsort_column = "na', b"[left\nsort_column = 1 =", b"\xff\xfe[left]", None],
    ids=["truncated", "syntax-error", "not-utf8", "directory"],
)
def test_damaged_state_gives_defaults(content: bytes | None) -> None:
    path = session_state._STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        path.mkdir()
    else:
        path.write_bytes(content)

    assert load_session_state() == SessionState()


def test_bad_values_are_ignored_individually() -> None:
    session_state._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[left]",
        'sort_column = "colour"',
        "sort_ascending = false",
        "[right]",
        'sort_column = "size"',
        'sort_ascending = "no"',
        "path = 5",
    ]
    session_state._STATE_FILE.write_text("\n".join(lines) + "\n")
    assert load_session_state() == SessionState(
        left=PanelState(sort_ascending=False),
        right=PanelState(sort_column="size"),
    )


def test_non_table_section_is_ignored() -> None:
    _write_state('left = 5\n[right]\nsort_column = "size"\n')

    assert load_session_state() == SessionState(right=PanelState(sort_column="size"))


def test_sort_column_names_match_sort_keys() -> None:
    assert set(SORT_COLUMN_NAMES) == {key.name.lower() for key in SortKey}


def test_save_failure_is_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "file").write_text("")
    monkeypatch.setattr(session_state, "_STATE_FILE", tmp_path / "file" / "state.toml")

    save_session_state(SessionState(right_path="/"))

    assert "session state" in caplog.text


def test_failed_write_keeps_previous_file(monkeypatch: pytest.MonkeyPatch) -> None:
    save_session_state(SessionState(right_path="/first"))
    path = session_state._STATE_FILE
    previous = path.read_bytes()

    def fail_replace(_src: str | bytes | os.PathLike[str], _dst: str | bytes | os.PathLike[str]) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    save_session_state(SessionState(right_path="/second"))

    assert path.read_bytes() == previous
    assert [p.name for p in path.parent.iterdir()] == ["state.toml"]


def test_unencodable_right_path_is_left_out() -> None:
    save_session_state(SessionState(left=PanelState("name", True), right_path="/home/user/\udcff"))

    assert load_session_state() == SessionState(left=PanelState("name", True))


def test_usable_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    regular = tmp_path / "file"
    regular.write_text("")

    assert usable_directory(str(directory)) == str(directory)
    assert usable_directory(str(regular)) is None
    assert usable_directory(str(tmp_path / "missing")) is None
    assert usable_directory(None) is None

    def deny(_path: str | bytes | os.PathLike[str]) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", deny)
    assert usable_directory(str(directory)) is None


def test_tests_do_not_use_real_directories() -> None:
    assert Path.home() / ".config" / "nova-navigator" != loader._APP_CONFIG_DIR
    assert Path.home() / ".local" not in session_state._STATE_FILE.parents
