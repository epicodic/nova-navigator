from __future__ import annotations

import pytest

from tests._utils.mock_filesystem import MockFilesystem


def test_base_filesystem_has_no_commands_capability() -> None:
    fs = MockFilesystem()
    assert fs.capabilities.commands is False


def test_base_filesystem_scheme_is_empty() -> None:
    assert MockFilesystem().scheme == ""


def test_base_exec_command_raises_not_implemented() -> None:
    fs = MockFilesystem()
    with pytest.raises(NotImplementedError):
        fs.exec_command("true", fs.path("/"))
