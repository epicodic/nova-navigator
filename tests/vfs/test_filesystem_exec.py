from __future__ import annotations

import pytest

from tests._utils.mock_filesystem import MockFilesystem


def test_base_exec_command_raises_not_implemented() -> None:
    fs = MockFilesystem()
    with pytest.raises(NotImplementedError):
        fs.exec_command("true", fs.path("/"))
