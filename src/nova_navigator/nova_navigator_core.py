from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePath

from nova_navigator import archive
from nova_navigator.config import conf_, get_config_file_path
from nova_navigator.dialogs import JobRegistry
from nova_navigator.icons import ICONS, IconSet
from nova_navigator.uri import register_common_schemes, vfspath_from_uri  # noqa: F401 (re-exported)
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import ArchiveFilesystem
from nova_widgets.menu import SYMBOL_TABLE, set_icon_provider

# ---------------------------------------------------------------------------
# PanelRef — reference to a file-browser panel
# ---------------------------------------------------------------------------


class PanelRef(Enum):
    """Reference to a file-browser panel, independent of the UI widget."""

    LEFT = "left"
    RIGHT = "right"
    ACTIVE = "active"


# ---------------------------------------------------------------------------
# NovaNavigatorCore
# ---------------------------------------------------------------------------


class NovaNavigatorCore:
    """Business logic layer — no Textual imports."""

    def __init__(self) -> None:
        super().__init__()
        conf_.load_all_configs()
        ICONS.load_icons(get_config_file_path("icons.csv"))
        ICONS.set_variant(IconSet.Variants.NERDFONT)
        set_icon_provider(ICONS.get_icon)
        SYMBOL_TABLE["checkbox"] = (ICONS.get_icon("checkbox"), ICONS.get_icon("checkbox_checked"))
        SYMBOL_TABLE["radio"] = (ICONS.get_icon("radio"), ICONS.get_icon("radio_checked"))
        register_common_schemes()

        self._job_registry = JobRegistry()

    async def open_editor(self, path: VPath) -> None:
        """Open *path* in an editor. Must be overridden by subclasses."""
        raise NotImplementedError

    async def open_path(self, path: VPath, panel: PanelRef = PanelRef.ACTIVE) -> None:
        """Open *path* — navigate, execute, or open with app as appropriate."""
        if path.stat.is_directory:
            await self.set_panel_directory(path, panel)
            return

        if archive.is_supported_archive(path.path):
            archive_vpath = VPath("/", ArchiveFilesystem(archive_parent=path.parent, archive=path))
            await self.set_panel_directory(archive_vpath, panel)
            return

        if path.stat.is_executable:
            mimetype = path.guess_mimetype()
            if mimetype is None or re.match(r".*/x-.*$", mimetype) is not None:
                await self.execute_command([path.path.as_posix()], path.parent.path)
                return

        open_cmd = conf_.filetypes.get_open_command_for_file_path(path.path)
        await self.execute_command(open_cmd, path.parent.path)

    async def execute_command(self, args: list[str], cwd: PurePath) -> None:
        """Execute *args* as a subprocess with *cwd* as the working directory. Must be overridden by subclasses."""
        raise NotImplementedError

    async def set_terminal_directory(self, path: VPath) -> None:
        """Change the terminal's working directory to *path*. Must be overridden by subclasses."""
        raise NotImplementedError

    async def set_panel_directory(self, path: VPath, panel: PanelRef) -> None:
        """Navigate the given panel to *path*. Must be overridden by subclasses."""
        raise NotImplementedError

    @property
    def job_registry(self) -> JobRegistry:
        return self._job_registry
