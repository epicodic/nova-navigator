from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import MagicMock

import pytest

from nova_navigator.clipboard import ClipboardOperation, PathClipboard
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems.local import LocalFilesystem


def _mock_app() -> MagicMock:
    app = MagicMock()
    app.copy_to_clipboard = MagicMock()
    return app


def _vpath(name: str) -> VPath:
    fs = LocalFilesystem.singleton()
    return VPath(PurePosixPath(f"/home/test/{name}"), fs)


def test_clipboard_starts_empty() -> None:
    cb = PathClipboard(_mock_app())
    assert cb.empty() is True


def test_get_on_empty_raises() -> None:
    cb = PathClipboard(_mock_app())
    with pytest.raises(ValueError, match="PathClipboard is empty"):
        cb.get()


def test_set_stores_paths_and_operation() -> None:
    cb = PathClipboard(_mock_app())
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.COPY)

    assert cb.empty() is False
    paths, op = cb.get()
    assert paths == (p,)
    assert op == ClipboardOperation.COPY


def test_set_writes_uri_to_osc52() -> None:
    app = _mock_app()
    cb = PathClipboard(app)
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.COPY)

    app.copy_to_clipboard.assert_called_once_with(p.uri)


def test_set_multiple_paths_writes_newline_separated_uris() -> None:
    app = _mock_app()
    cb = PathClipboard(app)
    p1 = _vpath("a.txt")
    p2 = _vpath("b.txt")
    cb.set((p1, p2), ClipboardOperation.CUT)

    app.copy_to_clipboard.assert_called_once_with(f"{p1.uri}\n{p2.uri}")


def test_clear_makes_empty() -> None:
    cb = PathClipboard(_mock_app())
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.COPY)
    cb.clear()

    assert cb.empty() is True


def test_clear_then_get_raises() -> None:
    cb = PathClipboard(_mock_app())
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.CUT)
    cb.clear()

    with pytest.raises(ValueError, match="PathClipboard is empty"):
        cb.get()
