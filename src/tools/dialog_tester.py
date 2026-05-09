"""Dialog tester — launch and visually test Nova Navigator dialogs in isolation.

CLI usage:
  uv run dialog_tester --list                       # list all available dialogs
  uv run dialog_tester ResponseDialog               # launch dialog interactively
  uv run dialog_tester ResponseDialog --screenshot  # render headlessly, print SVG to stdout
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Static

from nova_navigator.config import conf_
from nova_navigator.dialogs.connect_to_dialog import ConnectToDialog
from nova_navigator.dialogs.credentials_dialog import CredentialsDialog
from nova_navigator.dialogs.dialog import Dialog
from nova_navigator.dialogs.edit_bookmarks_dialog import EditBookmarksDialog
from nova_navigator.dialogs.edit_remotes_dialog import EditRemotesDialog
from nova_navigator.dialogs.file_dialog import FileDialog, FileDialogMode, FileTypeFilter
from nova_navigator.dialogs.files_dialog import CopyMoveFilesDialog, DeleteFilesDialog
from nova_navigator.dialogs.icon_picker_dialog import IconPickerDialog
from nova_navigator.dialogs.message_box import MessageBox
from nova_navigator.dialogs.response_dialog import OverwriteResponseDialog, ResponseDialog
from nova_navigator.dialogs.settings_dialog import SettingsDialog
from nova_navigator.nova_navigator_core import NovaNavigatorCore
from nova_navigator.response import Response
from nova_navigator.scheduler.context import ResponseRequest
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath

_fs = LocalFilesystem.singleton()

# Absolute path so CSS_PATH resolves regardless of cwd.
_TCSS = str(Path(__file__).parent.parent / "nova_navigator" / "nn.tcss")

_LauncherFn = Callable[[], Coroutine[Any, Any, str]]
_DialogFactory = Callable[[], Dialog]
_ResultFn = Callable[[Any, "Response | None"], str]


def _fmt(_dialog: Any, result: Response | None) -> str:
    return f"Result: {result}"


@dataclass
class DialogEntry:
    name: str
    description: str
    factory: _DialogFactory
    result_fn: _ResultFn = field(default=_fmt)

    @property
    def launcher(self) -> _LauncherFn:
        async def _run() -> str:
            dialog = self.factory()
            result = await dialog.run()
            return self.result_fn(dialog, result)

        return _run


# ── dialog registry ───────────────────────────────────────────────────────────

_ENTRIES: list[DialogEntry] = [
    DialogEntry(
        "ConnectToDialog",
        "Pick a saved remote connection (uses real config).",
        lambda: ConnectToDialog(conf_.remotes),
    ),
    DialogEntry(
        "SettingsDialog",
        "Edit all application settings (uses real config).",
        lambda: SettingsDialog(conf_.settings),
    ),
    DialogEntry(
        "CredentialsDialog",
        "Username + password prompt for SSH authentication.",
        lambda: CredentialsDialog(hostname="example.com", username="admin"),
        result_fn=lambda d, r: f"Result: {r}  creds={d.credentials if r == Response.OK else None}",
    ),
    DialogEntry(
        "MessageBox (default)",
        "Simple informational message with OK button.",
        lambda: MessageBox("Operation completed successfully.", title="Info"),
    ),
    DialogEntry(
        "MessageBox (success)",
        "Success message with green background.",
        lambda: MessageBox("File copied successfully.", title="Success", variant="success"),
    ),
    DialogEntry(
        "MessageBox (warning)",
        "Warning message with yellow background.",
        lambda: MessageBox("Disk space is running low.", title="Warning", variant="warning"),
    ),
    DialogEntry(
        "MessageBox (error)",
        "Error message with red background.",
        lambda: MessageBox("Operation failed: permission denied.", title="Error", variant="error"),
    ),
    DialogEntry(
        "MessageBox (confirm)",
        "Confirmation message with OK/Cancel — styled like the unknown-host prompt.",
        lambda: MessageBox(
            "The authenticity of host 'example.com' can't be established.\n"
            "ED25519 key fingerprint is SHA256:abc123xyz\n\nAdd to known hosts?",
            title="Unknown Host",
            buttons=[Response.OK, Response.CANCEL],
        ),
    ),
    DialogEntry(
        "ResponseDialog",
        "Simple yes/no response prompt.",
        lambda: ResponseDialog(
            ResponseRequest(
                title="Confirm action",
                expected_responses=[Response.YES, Response.NO],
                message="Do you want to proceed with this action?",
            )
        ),
    ),
    DialogEntry(
        "OverwriteResponseDialog",
        "File overwrite confirmation with source/destination info.",
        lambda: OverwriteResponseDialog(
            ResponseRequest(
                title="File already exists",
                expected_responses=[Response.YES, Response.ALL, Response.NO, Response.NONE],
                message="The destination file already exists.",
                dialog_type="overwrite",
                details={
                    "src_name": "document.pdf",
                    "src_size": 2_048_000,
                    "dst_name": "document.pdf",
                    "dst_size": 1_024_000,
                },
            )
        ),
    ),
    DialogEntry(
        "IconPickerDialog",
        "Grid-based icon selection dialog.",
        lambda: IconPickerDialog(title="Pick an Icon"),
    ),
    DialogEntry(
        "EditBookmarksDialog",
        "Full-screen bookmark editor (uses real user bookmarks).",
        lambda: EditBookmarksDialog(conf_.bookmarks),
    ),
    DialogEntry(
        "EditRemotesDialog",
        "Remote connection editor with real config.",
        lambda: EditRemotesDialog(conf_.remotes),
    ),
    DialogEntry(
        "CopyMoveFilesDialog (copy)",
        "Copy multiple files to a destination.",
        lambda: CopyMoveFilesDialog(
            source_paths=[VPath("/home/user/Documents/report.pdf", _fs), VPath("/home/user/Documents/notes.txt", _fs)],
            destination_path=VPath("/home/user/Downloads", _fs),
            move=False,
        ),
    ),
    DialogEntry(
        "CopyMoveFilesDialog (move)",
        "Move a single file — shows rename input.",
        lambda: CopyMoveFilesDialog(
            source_paths=[VPath("/home/user/Documents/report.pdf", _fs)],
            destination_path=VPath("/home/user/Downloads", _fs),
            move=True,
        ),
    ),
    DialogEntry(
        "DeleteFilesDialog",
        "Delete confirmation for multiple files.",
        lambda: DeleteFilesDialog(
            paths=[VPath("/home/user/Documents/old_report.pdf", _fs), VPath("/home/user/Downloads/archive.zip", _fs)]
        ),
    ),
    DialogEntry(
        "FileDialog (open)",
        "File picker in open mode — select an existing file.",
        lambda: FileDialog(mode=FileDialogMode.OPEN, start_path=Path.home(), title="Open File"),
        result_fn=lambda d, r: f"Result: {r}  path={d.selected_path}",
    ),
    DialogEntry(
        "FileDialog (save)",
        "File picker in save mode — with file-type filters.",
        lambda: FileDialog(
            mode=FileDialogMode.SAVE,
            start_path=Path.home(),
            title="Save File As",
            filters=[
                FileTypeFilter("Python files", ["*.py", "*.pyi"]),
                FileTypeFilter("Text files", ["*.txt", "*.md"]),
                FileTypeFilter("All files", ["*"]),
            ],
        ),
        result_fn=lambda d, r: f"Result: {r}  path={d.selected_path}",
    ),
    DialogEntry(
        "FileDialog (dir)",
        "Directory picker mode — select a folder.",
        lambda: FileDialog(mode=FileDialogMode.DIR, start_path=Path.home(), title="Select Directory"),
        result_fn=lambda d, r: f"Result: {r}  path={d.selected_path}",
    ),
]

_ENTRY_MAP: dict[str, DialogEntry] = {e.name.lower(): e for e in _ENTRIES}


def _resolve_entry(name: str) -> DialogEntry:
    entry = _ENTRY_MAP.get(name.lower())
    if entry is None:
        names = ", ".join(e.name for e in _ENTRIES)
        print(f"Error: unknown dialog '{name}'.\nAvailable: {names}", file=sys.stderr)
        sys.exit(1)
    return entry


# ── interactive runner ────────────────────────────────────────────────────────


class _RunnerApp(App[str | None]):
    """Minimal app that immediately launches one dialog then exits."""

    CSS_PATH = _TCSS

    def __init__(self, entry: DialogEntry) -> None:
        super().__init__()
        self._entry = entry

    def compose(self) -> ComposeResult:
        yield Static("")

    @work
    async def _run(self) -> None:
        await asyncio.sleep(0.05)  # let the app settle before pushing a modal
        try:
            result = await self._entry.launcher()
        except Exception as exc:  # noqa: BLE001
            result = f"Error: {exc}"
        self.exit(result)

    def on_mount(self) -> None:
        self._run()


# ── screenshot app ────────────────────────────────────────────────────────────


class _ScreenshotApp(App[str]):
    """Headless app that launches one dialog, captures a screenshot, then exits."""

    CSS_PATH = _TCSS

    def __init__(self, entry: DialogEntry) -> None:
        super().__init__()
        self._entry = entry
        self._svg: str = ""

    def compose(self) -> ComposeResult:
        yield Static(f"Loading {self._entry.name}…")

    @work
    async def _run(self) -> None:
        await asyncio.sleep(0.1)  # let the app settle
        dialog = self._entry.factory()
        self.push_screen(dialog)
        await asyncio.sleep(0.2)  # let the dialog render
        self._svg = self.export_screenshot()
        self.exit(self._svg)

    def on_mount(self) -> None:
        self._run()


# ── commands ──────────────────────────────────────────────────────────────────


def _cmd_list() -> None:
    console = Console()
    for entry in _ENTRIES:
        console.print(entry.name)
        console.print(f"  [grey50]{entry.description}[/grey50]")
        console.print()


def _cmd_run(name: str) -> None:
    entry = _resolve_entry(name)
    result = _RunnerApp(entry).run()
    if result is not None:
        print(result)


async def _cmd_screenshot(name: str) -> None:
    entry = _resolve_entry(name)
    app = _ScreenshotApp(entry)
    svg = await app.run_async(headless=True)
    if svg is None:
        svg = app._svg  # fallback if exit value lost
    print(svg)


# ── entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dialog_tester",
        description="Nova Navigator dialog tester.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run dialog_tester --list\n"
            "  uv run dialog_tester ResponseDialog\n"
            "  uv run dialog_tester ResponseDialog --screenshot\n"
        ),
    )
    parser.add_argument("--list", action="store_true", help="List all available dialogs and exit.")
    parser.add_argument("name", nargs="?", metavar="NAME", help="Dialog name to launch.")
    parser.add_argument("--screenshot", action="store_true", help="Render headlessly and print SVG to stdout.")
    args = parser.parse_args()

    if not args.list and not args.name:
        parser.print_help()
        sys.exit(0)

    NovaNavigatorCore()

    if args.list:
        _cmd_list()
        return

    if args.screenshot:
        asyncio.run(_cmd_screenshot(args.name))
    else:
        _cmd_run(args.name)


if __name__ == "__main__":
    main()
