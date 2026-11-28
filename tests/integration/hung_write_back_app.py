"""Run Nova Navigator in a process of its own and quit it; started by ``test_hung_write_back_exit.py``.

Usage: ``hung_write_back_app.py CASE WORK_DIR``, on a terminal, with HOME, TMPDIR and the XDG directories set to
temporary ones. ``CASE`` is ``nothing_pending`` (a plain quit), or ``hung_write``, ``hung_teardown`` or ``hung_exit``: an edited
copy of an archive member whose write-back never returns, quit through the write-back dialog. For ``hung_teardown``
a widget teardown never returns either, and for ``hung_exit`` the event loop stops before the terminal is given
back. Progress and failures are recorded in files below ``WORK_DIR``.
"""

from __future__ import annotations

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

from textual.pilot import Pilot
from textual.widgets import Button

from nova_navigator.dialogs import WriteBackRunningDialog
from nova_navigator.nova_navigator import NovaNavigator
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
    if _case == "hung_exit":

        async def _stop_the_loop(_self: NovaNavigator) -> None:
            _never_return("exit-entered")

        patch.object(NovaNavigator, "_on_exit_app", _stop_the_loop).start()
    # Only a case that hangs beyond the write may be ended by the backstop; the others must end without it.
    NovaNavigator.BACKSTOP_SECONDS = 2.0 if _case in ("hung_teardown", "hung_exit") else 600.0
    NovaNavigator().run(auto_pilot=_drive)
    _record("run-returned")


if __name__ == "__main__":
    main()
