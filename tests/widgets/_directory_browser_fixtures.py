"""Shared test infrastructure for DirectoryBrowser GUI tests."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from textual import events
from textual.app import App, ComposeResult
from textual.message import Message
from textual.pilot import Pilot

from nova_navigator.vfs.vpath import VPath
from nova_navigator.widgets.directory_browser import DirectoryBrowser
from nova_widgets.keymap.hint_bar import HintBar
from nova_widgets.keymap.registry import KeymapRegistry
from tests._utils.mock_filesystem import MockFilesystem, _FileNode  # type: ignore[attr-defined]


class DirectoryBrowserTestApp(App[None]):
    """Minimal app hosting a DirectoryBrowser and collecting its messages."""

    messages: list[Message]
    _keymap: KeymapRegistry

    def __init__(self, browser: DirectoryBrowser) -> None:
        super().__init__()
        self._browser = browser
        self.messages = []
        actions = list(DirectoryBrowser.ACTIONS)
        bindings = {a.name: a.initial_shortcut for a in actions if a.name and a.initial_shortcut is not None}
        self._keymap = KeymapRegistry(HintBar())
        self._keymap.reload(bindings, actions)

    def compose(self) -> ComposeResult:
        yield self._browser

    async def on_key(self, event: events.Key) -> None:
        await self._keymap.handle_key(event.key, self)

    def on_directory_browser_path_selected(self, message: DirectoryBrowser.PathSelected) -> None:
        self.messages.append(message)

    def on_directory_browser_item_changed(self, message: DirectoryBrowser.ItemChanged) -> None:
        self.messages.append(message)

    def on_directory_browser_context_menu(self, message: DirectoryBrowser.ContextMenu) -> None:
        self.messages.append(message)

    def on_directory_browser_focus(self, message: DirectoryBrowser.Focus) -> None:
        self.messages.append(message)


def make_browser(fs: MockFilesystem, path_str: str = "/home/user") -> DirectoryBrowser:
    """Construct a DirectoryBrowser pointed at *path_str* on *fs*."""
    path = VPath(PurePosixPath(path_str), fs)
    return DirectoryBrowser(path)


@asynccontextmanager
async def run_browser(
    fs: MockFilesystem,
    path_str: str = "/home/user",
) -> AsyncIterator[tuple[Pilot, DirectoryBrowser, list[Message]]]:
    """Async context manager that mounts a DirectoryBrowser and yields (pilot, browser, messages)."""
    browser = make_browser(fs, path_str)
    app = DirectoryBrowserTestApp(browser)
    async with app.run_test() as pilot:
        await pilot.pause()
        yield pilot, browser, app.messages


# ---------------------------------------------------------------------------
# MockFilesystem factory helpers
# ---------------------------------------------------------------------------


def flat_dir_fs() -> MockFilesystem:
    """A flat directory with two files and one subdirectory.

    /home/user/
        file_a.txt   (5 bytes)
        file_b.py    (10 bytes)
        subdir/
    """
    return MockFilesystem(
        {
            "/home/user/file_a.txt": b"hello",
            "/home/user/file_b.py": b"0123456789",
            "/home/user/subdir": None,
        }
    )


def hidden_files_fs() -> MockFilesystem:
    """A directory containing visible and hidden entries.

    /home/user/
        visible.txt
        .hidden
        .hidden_dir/
    """
    return MockFilesystem(
        {
            "/home/user/visible.txt": b"visible",
            "/home/user/.hidden": b"secret",
            "/home/user/.hidden_dir": None,
        }
    )


def nested_fs() -> MockFilesystem:
    """A directory with a subdirectory that itself has a file and a grandchild dir.

    /home/user/
        child/
            grandchild/
            data.bin
    """
    return MockFilesystem(
        {
            "/home/user/child/grandchild": None,
            "/home/user/child/data.bin": b"binary",
        }
    )


def sized_files_fs() -> MockFilesystem:
    """Files and directories with varying sizes and timestamps for sort tests.

    /home/user/
        alpha_dir/          (directory)
        beta_dir/           (directory)
        large.bin           (1000 bytes)
        medium.txt          (100 bytes)
        small.py            (10 bytes)
    """
    fs = MockFilesystem(
        {
            "/home/user/alpha_dir": None,
            "/home/user/beta_dir": None,
            "/home/user/large.bin": b"x" * 1000,
            "/home/user/medium.txt": b"y" * 100,
            "/home/user/small.py": b"z" * 10,
        }
    )
    # Assign distinct, ordered modification times so sort-by-modified tests are deterministic.
    base = time.time()
    for i, name in enumerate(["large.bin", "medium.txt", "small.py"]):
        node = fs._nodes[PurePosixPath(f"/home/user/{name}")]
        assert isinstance(node, _FileNode)
        node.modified = base + i  # large is oldest, small is newest
    return fs
