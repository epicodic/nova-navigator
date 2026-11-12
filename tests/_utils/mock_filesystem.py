"""In-memory Filesystem implementation for use in tests."""

from __future__ import annotations

import errno
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import override

from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath


@dataclass
class _FileNode:
    content: bytearray
    modified: float = field(default_factory=time.time)


@dataclass
class _DirNode:
    modified: float = field(default_factory=time.time)


@dataclass
class _LinkNode:
    target: str
    modified: float = field(default_factory=time.time)


_Node = _FileNode | _DirNode | _LinkNode
_MAX_HOPS = 40


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

    def __init__(self, node: _FileNode, error: Exception | None = None, on_close: Callable[[_FileNode], None] | None = None) -> None:
        self.close_count = 0
        self._node = node
        self._error = error
        self._on_close = on_close

    def write(self, data: bytes) -> int:
        if self._error is not None:
            raise self._error
        self._node.content.extend(data)
        return len(data)

    def close(self) -> None:
        self.close_count += 1
        if self._on_close is not None:
            self._on_close(self._node)
        else:
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
        links: dict[str, str] | None = None,
        symlinks: bool = True,
    ) -> None:
        self._nodes = {}
        self._symlinks = symlinks
        self._root_path = PurePosixPath(root)
        self._home_path = PurePosixPath(home)
        self._cwd_path = PurePosixPath(cwd)
        self._read_errors: dict[str, Exception] = read_errors or {}
        self._write_errors: dict[str, Exception] = write_errors or {}
        self.readers = []
        self.writers = []
        self._write_counter = 0

        for fixed_dir in (self._root_path, self._home_path, self._cwd_path):
            self._mkdir_p(fixed_dir)

        for path_str, content in (files or {}).items():
            path = PurePosixPath(path_str)
            self._mkdir_p(path.parent)
            if content is None:
                self._mkdir_p(path)
            else:
                self._nodes[path] = _FileNode(content=bytearray(content))

        for link_str, target in (links or {}).items():
            link_path = PurePosixPath(link_str)
            self._mkdir_p(link_path.parent)
            self._nodes[link_path] = _LinkNode(target=target)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mkdir_p(self, path: PurePosixPath) -> None:
        """Ensure *path* and all its ancestors exist as directory nodes."""
        for ancestor in [*list(reversed(path.parents)), path]:
            if ancestor not in self._nodes:
                self._nodes[ancestor] = _DirNode()

    def _resolve(self, path: PurePosixPath, *, follow_last: bool = True) -> PurePosixPath:
        """Return *path* with every link component replaced by its target (the last one only if *follow_last*)."""
        current = PurePosixPath("/")
        pending = list(path.parts[1:]) if path.anchor else list(path.parts)
        hops = 0
        while pending:
            part = pending.pop(0)
            if part == "..":
                current = current.parent
                continue
            candidate = current / part
            node = self._nodes.get(candidate)
            if isinstance(node, _LinkNode) and (pending or follow_last):
                hops += 1
                if hops > _MAX_HOPS:
                    raise OSError(errno.ELOOP, "Too many levels of symbolic links", str(path))
                target = PurePosixPath(node.target)
                if target.is_absolute():
                    current = PurePosixPath("/")
                    pending = list(target.parts[1:]) + pending
                else:
                    pending = list(target.parts) + pending
                continue
            current = candidate
        return current

    def _node(self, path: PurePosixPath) -> _Node:
        resolved = self._resolve(path)
        if resolved not in self._nodes:
            raise FileNotFoundError(f"No such file or directory: '{path}'")
        return self._nodes[resolved]

    def _raw_node(self, path: PurePosixPath) -> _Node:
        """Return the node at *path* without following a link in the last component."""
        resolved = self._resolve(path, follow_last=False)
        if resolved not in self._nodes:
            raise FileNotFoundError(f"No such file or directory: '{path}'")
        return self._nodes[resolved]

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

    def _touch(self, node: _FileNode) -> None:
        """Advance *node*'s modified time, guaranteeing it differs from any earlier write.

        Plain ``time.time()`` can return the same value for two writes issued back to
        back within one clock tick; a strictly increasing offset avoids that flakiness.
        """
        self._write_counter += 1
        node.modified = time.time() + self._write_counter

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
    async def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        posix = self._to_posix(path)
        self._dir_node(posix)  # raises if not a directory
        real = self._resolve(posix)
        children = [p for p in self._nodes if p.parent == real and p != real]
        for p in children:
            if cancel is not None and cancel.is_set():
                return
            vp = VPath(posix / p.name, self)
            vp._stat = self.stat(vp)
            yield vp

    @override
    def stat(self, path: VPath) -> Stat:
        posix = self._to_posix(path)
        raw = self._raw_node(posix)
        is_hidden = posix.name.startswith(".") and posix.name not in (".", "..")
        if isinstance(raw, _LinkNode):
            try:
                target = self._node(posix)
            except OSError:
                return Stat(
                    size=len(raw.target),
                    modified=raw.modified,
                    is_hidden=is_hidden,
                    is_symlink=True,
                    is_broken_symlink=True,
                )
            assert not isinstance(target, _LinkNode)
            return replace(self._stat_of(target, is_hidden), is_symlink=True)
        return self._stat_of(raw, is_hidden)

    @staticmethod
    def _stat_of(node: _FileNode | _DirNode, is_hidden: bool) -> Stat:
        if isinstance(node, _FileNode):
            return Stat(size=len(node.content), modified=node.modified, is_hidden=is_hidden, is_directory=False)
        return Stat(size=0, modified=node.modified, is_hidden=is_hidden, is_directory=True)

    @override
    def parent(self, path: VPath) -> VPath:
        posix = self._to_posix(path)
        return VPath(posix.parent, self)

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        self._assert_vpath(path1)
        return path1.filesystem == path2.filesystem

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
        posix = self._resolve(self._to_posix(path))
        if posix in self._nodes and isinstance(self._nodes[posix], _DirNode):
            raise IsADirectoryError(f"Is a directory: '{posix}'")
        self._dir_node(posix.parent)  # ensure parent directory exists
        node = _FileNode(content=bytearray())
        self._nodes[posix] = node
        error = self._write_errors.get(str(posix))
        writer = _Writer(node, error=error, on_close=self._touch)
        self.writers.append(writer)
        return writer

    @override
    def remove(self, path: VPath) -> None:
        posix = self._to_posix(path)
        if isinstance(self._raw_node(posix), _DirNode):
            raise IsADirectoryError(f"Is a directory: '{posix}'")
        del self._nodes[self._resolve(posix, follow_last=False)]

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        src_posix = self._resolve(self._to_posix(src_path), follow_last=False)
        dst_posix = self._resolve(self._to_posix(dst_path), follow_last=False)

        # Verify source exists
        src_node = self._raw_node(src_posix)

        # Verify destination parent exists and is a directory
        self._dir_node(dst_posix.parent)

        # If destination exists raise a FileExistsError
        if dst_posix in self._nodes:
            raise FileExistsError(f"File exists: '{dst_posix}'")

        # Move the node
        self._nodes[dst_posix] = src_node
        del self._nodes[src_posix]

        # If renaming a directory, update all child paths
        if isinstance(src_node, _DirNode):
            children_to_move = [(p, n) for p, n in self._nodes.items() if p != dst_posix and self._is_descendant(p, src_posix)]
            for old_path, node in children_to_move:
                # Calculate new path by replacing src prefix with dst
                relative = old_path.relative_to(src_posix)
                new_path = dst_posix / relative
                self._nodes[new_path] = node
                del self._nodes[old_path]

    def _is_descendant(self, path: PurePosixPath, ancestor: PurePosixPath) -> bool:
        """Check if *path* is a descendant of *ancestor*."""
        try:
            path.relative_to(ancestor)
            return True
        except ValueError:
            return False

    @override
    def rmdir(self, path: VPath) -> None:
        posix = self._resolve(self._to_posix(path), follow_last=False)
        node = self._raw_node(posix)
        if not isinstance(node, _DirNode):
            raise NotADirectoryError(f"Not a directory: '{posix}'")
        children = [p for p in self._nodes if p.parent == posix and p != posix]
        if children:
            raise OSError(f"Directory not empty: '{posix}'")
        del self._nodes[posix]

    @override
    def mkdir(self, path: VPath) -> None:
        posix = self._to_posix(path)
        self._dir_node(posix.parent)  # raises if parent doesn't exist or isn't a directory
        target = self._resolve(posix.parent) / posix.name
        if target in self._nodes:
            raise FileExistsError(f"File exists: '{posix}'")
        self._nodes[target] = _DirNode()

    def exists(self, path: str) -> bool:
        """Return True if *path* exists in this filesystem."""
        return PurePosixPath(path) in self._nodes

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        posix = self._to_posix(path)
        node = self._node(posix)
        if stat.modified >= 0:
            node.modified = stat.modified

    @override
    def readlink(self, path: VPath) -> str:
        node = self._raw_node(self._to_posix(path))
        if isinstance(node, _LinkNode):
            return node.target
        raise OSError(errno.EINVAL, f"Not a symbolic link: '{path}'")

    @override
    def supports_symlinks(self) -> bool:
        return self._symlinks

    @override
    def symlink(self, path: VPath, target: str) -> None:
        posix = self._to_posix(path)
        if not self._symlinks:
            raise OSError(errno.ENOTSUP, "MockFilesystem has symbolic links disabled", str(posix))
        self._dir_node(posix.parent)
        link_path = self._resolve(posix.parent) / posix.name
        if link_path in self._nodes:
            raise FileExistsError(f"File exists: '{posix}'")
        self._nodes[link_path] = _LinkNode(target=target)

    @override
    def real_path(self, path: VPath) -> VPath | None:
        posix = self._to_posix(path)
        resolved = self._resolve(posix)
        if resolved not in self._nodes:
            raise FileNotFoundError(f"No such file or directory: '{posix}'")
        return VPath(resolved, self)

    @override
    def refresh(self, path: VPath | None = None) -> None:
        pass  # no caching in MockFilesystem
