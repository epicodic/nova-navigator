"""Shared test helpers for local-copy tests."""

from __future__ import annotations

from pathlib import PurePath

from nova_navigator.vfs.vpath import VPath

from .mock_filesystem import MockFilesystem


class SchemeFs(MockFilesystem):
    """MockFilesystem whose URIs look like an SSH host, for path-mapping tests."""

    def uri_for_path(self, path: PurePath) -> str:
        return f"ssh://user@host:2222{path}"


def overwrite(fs: MockFilesystem, path: str, data: bytes) -> None:
    """Write *data* to *path* through *fs*, advancing the mock's recorded mtime."""
    writer = fs.write(VPath(path, fs))
    writer.write(data)
    writer.close()
