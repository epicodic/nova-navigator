"""In-memory Filesystem implementation for use in tests."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import override

from nova_navigator.vfs2.filesystem import Filesystem
from nova_navigator.vfs2.types import Stat
from nova_navigator.vfs2.vpath import VPath


@dataclass
class _FileNode:
    content: bytearray
    modified: float = field(default_factory=time.time)


@dataclass
class _DirNode:
    modified: float = field(default_factory=time.time)


_Node = _FileNode | _DirNode


class _Reader:
    close_count: int

    def __init__(self, data: bytes, error: Exception | None = None) -> None:
        self.close_count = 0
        self._pos = 0
        self._data = data
        self._error = error

    def read(self, size: int) -> bytes:
        if self._error is not None:
            raise self._error
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self.close_count += 1


class _Writer:
    close_count: int

    def __init__(self, node: _FileNode, error: Exception | None = None) -> None:
        self.close_count = 0
        self._node = node
        self._error = error

    def write(self, data: bytes) -> int:
        if self._error is not None:
            raise self._error
        self._node.content.extend(data)
        return len(data)

    def close(self) -> None:
        self.close_count += 1
        self._node.modified = time.time()


class MockFilesystem(Filesystem):
    r"""Virtual in-memory filesystem for testing.

    Construct with a flat dict of absolute paths to byte content::

        fs = MockFilesystem({
            "/home/user/hello.txt": b"hello world",
            "/home/user/sub/data.bin": b"\\x00\\x01\\x02",
        })

    Directories are created implicitly for every parent path that appears.
    An explicit empty directory can be created by mapping its path to ``None``::

        fs = MockFilesystem({"/empty/dir": None})
    """

    _nodes: dict[PurePosixPath, _Node]
    readers: list[_Reader]
    writers: list[_Writer]

    def __init__(
        self,
        files: dict[str, bytes | None] | None = None,
        *,
        root: str = "/",
        home: str = "/home/user",
        cwd: str = "/home/user",
        read_errors: dict[str, Exception] | None = None,
        write_errors: dict[str, Exception] | None = None,
    ) -> None:
        self._nodes = {}
        self._root_path = PurePosixPath(root)
        self._home_path = PurePosixPath(home)
        self._cwd_path = PurePosixPath(cwd)
        self._read_errors: dict[str, Exception] = read_errors or {}
        self._write_errors: dict[str, Exception] = write_errors or {}
        self.readers = []
        self.writers = []

        for fixed_dir in (self._root_path, self._home_path, self._cwd_path):
            self._mkdir_p(fixed_dir)

        for path_str, content in (files or {}).items():
            path = PurePosixPath(path_str)
            self._mkdir_p(path.parent)
            if content is None:
                self._mkdir_p(path)
            else:
                self._nodes[path] = _FileNode(content=bytearray(content))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mkdir_p(self, path: PurePosixPath) -> None:
        """Ensure *path* and all its ancestors exist as directory nodes."""
        for ancestor in [*list(reversed(path.parents)), path]:
            if ancestor not in self._nodes:
                self._nodes[ancestor] = _DirNode()

    def _node(self, path: PurePosixPath) -> _Node:
        if path not in self._nodes:
            raise FileNotFoundError(f"No such file or directory: '{path}'")
        return self._nodes[path]

    def _file_node(self, path: PurePosixPath) -> _FileNode:
        node = self._node(path)
        if not isinstance(node, _FileNode):
            raise IsADirectoryError(f"Is a directory: '{path}'")
        return node

    def _dir_node(self, path: PurePosixPath) -> _DirNode:
        node = self._node(path)
        if not isinstance(node, _DirNode):
            raise NotADirectoryError(f"Not a directory: '{path}'")
        return node

    def _to_posix(self, vpath: VPath) -> PurePosixPath:
        self._assert_vpath(vpath)
        return PurePosixPath(str(vpath.path))

    # ------------------------------------------------------------------
    # Filesystem ABC
    # ------------------------------------------------------------------

    @override
    def cwd(self) -> VPath:
        return VPath(self._cwd_path, self)

    @override
    def root(self) -> VPath:
        return VPath(self._root_path, self)

    @override
    def home(self) -> VPath:
        return VPath(self._home_path, self)

    @override
    def iterdir(self, path: VPath) -> list[VPath]:
        posix = self._to_posix(path)
        self._dir_node(posix)  # raises if not a directory
        children = [p for p in self._nodes if p.parent == posix and p != posix]
        return [VPath(p, self) for p in children]

    @override
    def stat(self, path: VPath) -> Stat:
        posix = self._to_posix(path)
        node = self._node(posix)
        name = posix.name
        is_hidden = name.startswith(".") and name not in (".", "..")
        if isinstance(node, _FileNode):
            return Stat(
                size=len(node.content),
                modified=node.modified,
                is_hidden=is_hidden,
                is_directory=False,
            )
        return Stat(
            size=0,
            modified=node.modified,
            is_hidden=is_hidden,
            is_directory=True,
        )

    @override
    def parent(self, path: VPath) -> VPath:
        posix = self._to_posix(path)
        return VPath(posix.parent, self)

    @override
    def read(self, path: VPath) -> _Reader:
        posix = self._to_posix(path)
        node = self._file_node(posix)
        error = self._read_errors.get(str(posix))
        reader = _Reader(bytes(node.content), error=error)
        self.readers.append(reader)
        return reader

    @override
    def write(self, path: VPath) -> _Writer:
        posix = self._to_posix(path)
        self._dir_node(posix.parent)  # ensure parent directory exists
        node = _FileNode(content=bytearray())
        self._nodes[posix] = node
        error = self._write_errors.get(str(posix))
        writer = _Writer(node, error=error)
        self.writers.append(writer)
        return writer

    @override
    def remove(self, path: VPath) -> None:
        posix = self._to_posix(path)
        self._file_node(posix)  # raises if directory or missing
        del self._nodes[posix]

    @override
    def rmdir(self, path: VPath) -> None:
        posix = self._to_posix(path)
        self._dir_node(posix)  # raises if not a directory
        children = [p for p in self._nodes if p.parent == posix and p != posix]
        if children:
            raise OSError(f"Directory not empty: '{posix}'")
        del self._nodes[posix]

    @override
    def mkdir(self, path: VPath) -> None:
        posix = self._to_posix(path)
        self._dir_node(posix.parent)  # raises if parent doesn't exist or isn't a directory
        if posix in self._nodes:
            raise FileExistsError(f"File exists: '{posix}'")
        self._nodes[posix] = _DirNode()
