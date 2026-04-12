"""Dialog tester — launch and visually test Nova Navigator dialogs in isolation.

CLI usage:
  uv run dialog_tester --list                       # print all dialogs as JSON
  uv run dialog_tester DecisionDialog               # launch dialog interactively
  uv run dialog_tester DecisionDialog --screenshot  # render headlessly, print SVG to stdout
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Static

from nova_navigator.config import conf_
from nova_navigator.decision import Decision
from nova_navigator.dialogs.decision_dialog import DecisionDialog, OverwriteDecisionDialog
from nova_navigator.dialogs.edit_bookmarks_dialog import EditBookmarksDialog
from nova_navigator.dialogs.edit_remotes_dialog import EditRemotesDialog
from nova_navigator.dialogs.files_dialog import CopyMoveFilesDialog, DeleteFilesDialog
from nova_navigator.dialogs.icon_picker_dialog import IconPickerDialog
from nova_navigator.nova_navigator_core import NovaNavigatorCore
from nova_navigator.scheduler.context import DecisionRequest
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath

_fs = LocalFilesystem.singleton()

_LauncherFn = Callable[[App[Any]], Coroutine[Any, Any, str]]


@dataclass
class DialogEntry:
    name: str
    description: str
    launcher: _LauncherFn


# ── launcher functions ────────────────────────────────────────────────────────


async def _launch_decision(app: App[Any]) -> str:
    request = DecisionRequest(
        title="Confirm action",
        expected_decisions=[Decision.YES, Decision.NO],
        message="Do you want to proceed with this action?",
    )
    result = await app.push_screen_wait(DecisionDialog(request))
    return f"Result: {result}"


async def _launch_overwrite(app: App[Any]) -> str:
    request = DecisionRequest(
        title="File already exists",
        expected_decisions=[Decision.YES, Decision.ALL, Decision.NO, Decision.NONE],
        message="The destination file already exists.",
        dialog_type="overwrite",
        details={
            "src_name": "document.pdf",
            "src_size": 2_048_000,
            "dst_name": "document.pdf",
            "dst_size": 1_024_000,
        },
    )
    result = await app.push_screen_wait(OverwriteDecisionDialog(request))
    return f"Result: {result}"


async def _launch_icon_picker(app: App[Any]) -> str:
    result = await app.push_screen_wait(IconPickerDialog(title="Pick an Icon"))
    return f"Result: {result}"


async def _launch_edit_bookmarks(app: App[Any]) -> str:
    result = await app.push_screen_wait(EditBookmarksDialog(conf_.bookmarks))
    return f"Result: {result}"


async def _launch_edit_remotes(app: App[Any]) -> str:
    result = await app.push_screen_wait(EditRemotesDialog(conf_.remotes))
    return f"Result: {result}"


async def _launch_copy_files(app: App[Any]) -> str:
    src = [VPath("/home/user/Documents/report.pdf", _fs), VPath("/home/user/Documents/notes.txt", _fs)]
    dst = VPath("/home/user/Downloads", _fs)
    result = await app.push_screen_wait(CopyMoveFilesDialog(source_paths=src, destination_path=dst, move=False))
    return f"Result: {result}"


async def _launch_move_file(app: App[Any]) -> str:
    src = [VPath("/home/user/Documents/report.pdf", _fs)]
    dst = VPath("/home/user/Downloads", _fs)
    result = await app.push_screen_wait(CopyMoveFilesDialog(source_paths=src, destination_path=dst, move=True))
    return f"Result: {result}"


async def _launch_delete_files(app: App[Any]) -> str:
    paths = [VPath("/home/user/Documents/old_report.pdf", _fs), VPath("/home/user/Downloads/archive.zip", _fs)]
    result = await app.push_screen_wait(DeleteFilesDialog(paths=paths))
    return f"Result: {result}"


# ── dialog registry ───────────────────────────────────────────────────────────

_ENTRIES: list[DialogEntry] = [
    DialogEntry("DecisionDialog", "Simple yes/no decision prompt.", _launch_decision),
    DialogEntry(
        "OverwriteDecisionDialog",
        "File overwrite confirmation with source/destination info.",
        _launch_overwrite,
    ),
    DialogEntry("IconPickerDialog", "Grid-based icon selection dialog.", _launch_icon_picker),
    DialogEntry(
        "EditBookmarksDialog",
        "Full-screen bookmark editor (uses real user bookmarks).",
        _launch_edit_bookmarks,
    ),
    DialogEntry("EditRemotesDialog", "Remote connection editor with real config.", _launch_edit_remotes),
    DialogEntry("CopyMoveFilesDialog (copy)", "Copy multiple files to a destination.", _launch_copy_files),
    DialogEntry("CopyMoveFilesDialog (move)", "Move a single file — shows rename input.", _launch_move_file),
    DialogEntry("DeleteFilesDialog", "Delete confirmation for multiple files.", _launch_delete_files),
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

    def __init__(self, entry: DialogEntry) -> None:
        super().__init__()
        self._entry = entry

    def compose(self) -> ComposeResult:
        yield Static("")

    @work
    async def _run(self) -> None:
        await asyncio.sleep(0.05)  # let the app settle before pushing a modal
        try:
            result = await self._entry.launcher(self)
        except Exception as exc:  # noqa: BLE001
            result = f"Error: {exc}"
        self.exit(result)

    def on_mount(self) -> None:
        self._run()


# ── screenshot app ────────────────────────────────────────────────────────────


class _ScreenshotApp(App[str]):
    """Headless app that launches one dialog, captures a screenshot, then exits."""

    def __init__(self, entry: DialogEntry) -> None:
        super().__init__()
        self._entry = entry
        self._svg: str = ""

    def compose(self) -> ComposeResult:
        yield Static(f"Loading {self._entry.name}…")

    @work
    async def _run(self) -> None:
        await asyncio.sleep(0.1)  # let the app settle
        with contextlib.suppress(Exception):
            await self._entry.launcher(self)
        self._svg = self.export_screenshot()
        self.exit(self._svg)

    def on_mount(self) -> None:
        self._run()


# ── commands ──────────────────────────────────────────────────────────────────


def _cmd_list() -> None:
    entries = [{"name": e.name, "description": e.description} for e in _ENTRIES]
    print(json.dumps(entries, indent=2))


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
            "  uv run dialog_tester DecisionDialog\n"
            "  uv run dialog_tester DecisionDialog --screenshot\n"
        ),
    )
    parser.add_argument("--list", action="store_true", help="Print all dialogs as JSON and exit.")
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
