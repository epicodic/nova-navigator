"""External mirror launcher tests."""

from pathlib import Path
from unittest.mock import MagicMock

from nova_navigator.editing.launcher import EditingLauncher, expand_command
from nova_navigator.editing.model import EditingSession, SessionState, SourceVersion


def _session(path: Path) -> EditingSession:
    return EditingSession(
        "0ac5462d-7e31-4240-b16c-3bedbbc5ecb9",
        "ssh://host/file.txt",
        None,
        None,
        path,
        None,
        "digest",
        SourceVersion("digest", "digest", 1),
        False,
        False,
        SessionState.READY,
    )


def test_expand_command_replaces_file_and_directory(tmp_path: Path) -> None:
    mirror = tmp_path / "file.txt"
    assert expand_command(["editor", "%f", "--dir=%d"], mirror) == ["editor", str(mirror), f"--dir={tmp_path}"]


def test_launcher_uses_argument_list_and_mirror_directory(tmp_path: Path) -> None:
    process = MagicMock()
    popen = MagicMock(return_value=process)
    EditingLauncher(popen).launch(_session(tmp_path / "file.txt"), ["editor", "%f"])
    popen.assert_called_once_with(["editor", str(tmp_path / "file.txt")], cwd=tmp_path)


def test_waiting_launcher_finishes_only_after_successful_exit(tmp_path: Path) -> None:
    process = MagicMock()
    process.wait.return_value = 0
    finish = MagicMock()
    EditingLauncher(MagicMock(return_value=process)).launch_waiting(_session(tmp_path / "file.txt"), ["editor"], finish)
    finish.assert_called_once_with("0ac5462d-7e31-4240-b16c-3bedbbc5ecb9")
