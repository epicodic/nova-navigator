"""Shared test helpers for local-copy tests."""

from __future__ import annotations

from pathlib import PurePath

from .mock_filesystem import MockFilesystem


class SchemeFs(MockFilesystem):
    """MockFilesystem whose URIs look like an SSH host, for path-mapping tests."""

    def uri_for_path(self, path: PurePath) -> str:
        return f"ssh://user@host:2222{path}"
