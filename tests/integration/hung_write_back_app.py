"""Run Nova Navigator in a process of its own and quit it; started by ``test_hung_write_back_exit.py``.

Usage: ``hung_write_back_app.py CASE WORK_DIR``, on a terminal, with HOME, TMPDIR and the XDG directories set to
temporary ones. ``CASE`` is ``nothing_pending`` (a plain quit), or one of these, each with an edited copy of an archive
member whose write-back never returns, quit through the write-back dialog:

- ``hung_write``: nothing else goes wrong.
- ``stubborn_shell``: the panel shell ignores the hangup (the test's ``.bashrc`` makes it so).
- ``hung_teardown``: a widget teardown never returns either.
- ``hung_exit``: the event loop stops before the terminal is given back.
- ``blocked_output``: as ``hung_exit``, and nothing reads what is written to stderr.
- ``hung_unsynced``, ``hung_session_save``: reading the unsynced state, or saving the session state, never returns.
- ``teardown_error``: textual's teardown raises.

Progress and failures are recorded in files below ``WORK_DIR``.
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import sys
import threading
import traceback
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Button

from nova_navigator.dialogs import WriteBackRunningDialog
from nova_navigator.local_copies import LocalCopyManager
from nova_navigator.nova_navigator import MainScreen, NovaNavigator
from nova_navigator.terminal import Terminal
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.local_copy import LocalCopy

_LIFETIME_SECONDS = 90  # a process left behind by a failed test ends by itself

_case = sys.argv[1]
_work = Path(sys.argv[2])


def _record(name: str, text: str = "") -> None:
    (_work / name).write_text(text)


def _never_return(marker: str) -> None:
    _record(marker)
    threading.Event().wait()


def _hung_write_back(*_args: object, **_kwargs: object) -> None:
    _never_return("write-entered")


def _block_stderr() -> None:
    """Point stderr at a full pipe that nobody reads."""
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)
    with contextlib.suppress(BlockingIOError):
        while True:
            os.write(write_end, b"x" * 4096)
    os.set_blocking(write_end, True)
    os.dup2(write_end, 2)
    os.set_inheritable(read_end, False)  # stays open, so a write blocks instead of failing


def _teardown_error(_self: object) -> None:
    raise RuntimeError("boom-in-teardown")


async def _until(pilot: Pilot[None], condition: Callable[[], object]) -> None:
    for _ in range(300):
        if condition():
            return
        await pilot.pause(0.1)
    raise AssertionError("condition not met within 30 s")


async def _quit_with_hung_write_back(pilot: Pilot[None], app: NovaNavigator) -> None:
    archive = _work / "data.zip"
    with zipfile.ZipFile(archive, "w") as content:
        content.writestr("a.txt", "original\n")
    local = LocalFilesystem.singleton()
    mounted = ArchiveFilesystem(VPath(_work, local), VPath(archive, local))
    entry = await app.run_worker(app.local_copies.open(mounted.root() / "a.txt")).wait()
    assert entry is not None
    entry.copy.path.write_text("EDITED\n")
    await _until(pilot, (_work / "write-entered").exists)

    await pilot.press("ctrl+q")
    await _until(pilot, lambda: app.screen.query("#IGNORE"))
    await pilot.click("#IGNORE")  # "Quit and keep files" of the quit prompt, which now waits for the write-back
    await pilot.pause()
    await pilot.press("ctrl+q")
    await _until(pilot, lambda: isinstance(app.screen, WriteBackRunningDialog))
    if _case == "stubborn_shell":
        await _until(pilot, (Path.home() / "shell-ignores-hangup").exists)
    if _case == "hung_unsynced":
        patch.object(LocalCopyManager, "unsynced", lambda _self: _never_return("unsynced-entered")).start()
    if _case == "hung_session_save":
        patch.object(MainScreen, "save_session_state", lambda _self: _never_return("save-entered")).start()
    if _case == "teardown_error":
        patch.object(App, "_shutdown", _teardown_error).start()
    app.screen.query_one("#IGNORE", Button).press()


async def _drive(pilot: Pilot[Any]) -> None:
    try:
        app = pilot.app
        assert isinstance(app, NovaNavigator)
        await pilot.pause()
        if _case == "nothing_pending":
            await pilot.press("ctrl+q")
        else:
            await _quit_with_hung_write_back(pilot, app)
    except BaseException:
        _record("failure", traceback.format_exc())
        raise
    finally:
        if (_work / "failure").exists():
            os._exit(3)  # at once, also with a write hung


def main() -> None:
    faulthandler.dump_traceback_later(_LIFETIME_SECONDS, exit=True)
    if _case != "nothing_pending":
        patch.object(LocalCopy, "write_back", _hung_write_back).start()
    if _case == "hung_teardown":
        patch.object(Terminal, "on_unmount", lambda _self: _never_return("teardown-entered")).start()
    if _case in ("hung_exit", "blocked_output"):

        async def _stop_the_loop(_self: NovaNavigator) -> None:
            if _case == "blocked_output":
                _block_stderr()
            _never_return("exit-entered")

        patch.object(NovaNavigator, "_on_exit_app", _stop_the_loop).start()
    # Only a case that hangs beyond the write may be ended by the backstop; the others must end without it.
    hangs_beyond_the_write = _case in ("hung_teardown", "hung_exit", "blocked_output", "hung_unsynced", "hung_session_save")
    NovaNavigator.BACKSTOP_SECONDS = 2.0 if hangs_beyond_the_write else 600.0
    NovaNavigator().run(auto_pilot=_drive)
    _record("run-returned")


if __name__ == "__main__":
    main()
