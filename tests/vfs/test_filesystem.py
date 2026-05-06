from __future__ import annotations

from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem


class _SymlinkMockFilesystem(MockFilesystem):
    def stat(self, path: VPath) -> Stat:
        stat = super().stat(path)
        if path.name in {"absolute-link", "relative-link"}:
            return Stat(
                size=stat.size,
                modified=stat.modified,
                is_hidden=stat.is_hidden,
                is_directory=stat.is_directory,
                is_symlink=True,
            )
        return stat

    def readlink(self, path: VPath) -> str:
        if path.name == "absolute-link":
            return "/var/log/app.log"
        if path.name == "relative-link":
            return "../target.txt"
        return super().readlink(path)


def test_resolve_link_returns_absolute_target_path() -> None:
    fs = _SymlinkMockFilesystem(files={"/home/user/absolute-link": b""})

    resolved = fs.resolve_link(fs.path("/home/user/absolute-link"))

    assert resolved == fs.path("/var/log/app.log")


def test_resolve_link_returns_relative_target_path() -> None:
    fs = _SymlinkMockFilesystem(files={"/home/user/sub/relative-link": b""})

    resolved = fs.resolve_link(fs.path("/home/user/sub/relative-link"))

    assert resolved == fs.path("/home/user/sub") / "../target.txt"
