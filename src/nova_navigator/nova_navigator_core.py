from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path, PurePath

from nova_navigator import archive
from nova_navigator.config import conf_, get_config_file_path
from nova_navigator.config.settings import NerdFontMode
from nova_navigator.dialogs import JobRegistry
from nova_navigator.editing.archive_mount import open_archive_mount
from nova_navigator.editing.launcher import expand_command
from nova_navigator.editing.service import EditingService
from nova_navigator.editing.store import DEFAULT_BASE_DIR, SessionStore
from nova_navigator.icons import ICONS, IconSet
from nova_navigator.nerd_font_detect import detect_nerd_font
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.scheme_registry import register_common_schemes, vfspath_from_uri  # noqa: F401 (re-exported)
from nova_widgets.menu import SYMBOL_TABLE, set_icon_provider

# ---------------------------------------------------------------------------
# NerdFont variant resolution
# ---------------------------------------------------------------------------


def _resolve_nerd_font_variant(mode: NerdFontMode) -> IconSet.Variants:
    """Return the icon variant implied by *mode*.

    ``YES`` and ``NO`` are explicit overrides.
    ``AUTO`` delegates to :func:`~nova_navigator.nerd_font_detect.detect_nerd_font`.
    """
    if mode is NerdFontMode.YES:
        return IconSet.Variants.NERDFONT
    if mode is NerdFontMode.NO:
        return IconSet.Variants.UNICODE
    return IconSet.Variants.NERDFONT if detect_nerd_font() else IconSet.Variants.UNICODE


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
        variant = _resolve_nerd_font_variant(conf_.settings.general.use_nerd_font)
        ICONS.set_variant(variant)
        set_icon_provider(ICONS.get_icon)
        SYMBOL_TABLE["checkbox"] = (ICONS.get_icon("checkbox"), ICONS.get_icon("checkbox_checked"))
        SYMBOL_TABLE["radio"] = (ICONS.get_icon("radio"), ICONS.get_icon("radio_checked"))
        register_common_schemes()

        self._job_registry = JobRegistry()
        self.editing_service = EditingService(SessionStore())
        self.editing_recovery_issues = self.editing_service.recover()[1]

    async def open_editor(self, path: VPath) -> None:
        """Open *path* in an editor. Must be overridden by subclasses."""
        raise NotImplementedError

    async def open_path(self, path: VPath, panel: PanelRef = PanelRef.ACTIVE) -> None:
        """Open *path* — navigate, execute, or open with app as appropriate."""
        if path.stat.is_directory:
            await self.set_panel_directory(path, panel)
            return

        if archive.is_supported_archive(path.path):
            archive_filesystem = await asyncio.to_thread(
                open_archive_mount,
                path,
                DEFAULT_BASE_DIR / "archive-mounts",
                lambda: False,
            )
            archive_vpath = VPath("/", archive_filesystem)
            await self.set_panel_directory(archive_vpath, panel)
            return

        if path.stat.is_executable:
            mimetype = path.guess_mimetype()
            if mimetype is None or re.match(r".*/x-.*$", mimetype) is not None:
                await self.execute_command([path.path.as_posix()], path.parent.path)
                return

        if not isinstance(path.filesystem.unwrap(), LocalFilesystem):
            rule = conf_.filetypes.get_open_rule_for_file_path(path.path)
            await self.open_external_editing_session(path, rule.template or rule.command, rule.wait_for_exit)
            return

        open_cmd = conf_.filetypes.get_open_command_for_file_path(path.path)
        await self.execute_command(open_cmd, path.parent.path)

    async def open_external_editing_session(self, path: VPath, command: list[str], wait_for_exit: bool) -> None:
        """Prepare a mirror and run its configured external command.

        Concrete UI classes override this to keep detached applications outside
        Textual suspension while retaining a visible editing session.
        """
        session = await asyncio.to_thread(self.editing_service.prepare, path, wait_for_exit=wait_for_exit)
        mirror_command = self.mirror_command(path, command, session.mirror_path)
        await self.execute_command(mirror_command, session.mirror_path.parent)
        if wait_for_exit:
            await asyncio.to_thread(self.editing_service.finish, session.session_id)

    @staticmethod
    def mirror_command(path: VPath, command: list[str], mirror: Path) -> list[str]:
        """Expand a filetype command against its local mirror path."""
        del path
        return expand_command(command, mirror)

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
