"""Terminal factory for archive filesystems."""

from __future__ import annotations

from nova_navigator.config import conf_
from nova_navigator.plugins import FilesystemPlugin
from nova_navigator.terminal.terminal import Terminal
from nova_navigator.terminal.vfs_shell import VfsShellDriver, VirtualPtyBackend
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem


async def make_archive_terminal(fs: Filesystem) -> Terminal | None:
    """Create a virtual shell rooted in an archive filesystem."""
    backend = VirtualPtyBackend(fs, fs.home())
    return Terminal(
        "vfs",
        backend=backend,
        driver=VfsShellDriver(),
        keep_alive=True,
        scrollback_lines=conf_.settings.terminal.scrollback_lines,
    )


ARCHIVE_PLUGIN = FilesystemPlugin(
    scheme="archive",
    fs_type=ArchiveFilesystem,
    terminal_factory=make_archive_terminal,
)
