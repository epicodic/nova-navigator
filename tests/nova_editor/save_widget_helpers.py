"""Helpers of the widget save tests: a recording host, a gated `SaveIo` and a bounded wait for the end of a save."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from textual.message import Message
from textual.pilot import Pilot

from nova_editor.core.save import SaveIo, SaveSettings
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import HostApp, wait_until

SETTINGS = SaveSettings(chunk=7, fsync_every=64)
GATE_SECONDS = 10.0


@dataclass
class Gate:
    """Blocks the writes of a save until `release()`; `reached` is set when the first write arrives."""

    reached: threading.Event = field(default_factory=threading.Event)
    opened: threading.Event = field(default_factory=threading.Event)

    def release(self) -> None:
        self.opened.set()

    def io(self) -> SaveIo:
        real = SaveIo()

        def write(fd: int, data: bytes | memoryview) -> int:
            self.reached.set()
            assert self.opened.wait(GATE_SECONDS)
            return real.write(fd, data)

        return SaveIo(write=write)


class SaveHost(HostApp):
    """Host that records every save message of the widget with its arrival time."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__(widget)
        self.saves: list[tuple[float, Message]] = []
        self.refused: list[str] = []

    def _note(self, message: Message) -> None:
        self.saves.append((time.monotonic(), message))

    def on_nova_text_area_saved(self, message: NovaTextArea.Saved) -> None:
        self._note(message)

    def on_nova_text_area_save_failed(self, message: NovaTextArea.SaveFailed) -> None:
        self._note(message)

    def on_nova_text_area_save_cancelled(self, message: NovaTextArea.SaveCancelled) -> None:
        self._note(message)

    def on_nova_text_area_save_progress(self, message: NovaTextArea.SaveProgress) -> None:
        self._note(message)

    def on_nova_text_area_save_needs_confirmation(self, message: NovaTextArea.SaveNeedsConfirmation) -> None:
        self._note(message)

    def on_nova_text_area_edit_refused(self, message: NovaTextArea.EditRefused) -> None:
        self.refused.append(message.reason)

    def progress(self) -> list[NovaTextArea.SaveProgress]:
        return [message for _, message in self.saves if isinstance(message, NovaTextArea.SaveProgress)]

    def terminals(self) -> list[Message]:
        kinds = (NovaTextArea.Saved, NovaTextArea.SaveFailed, NovaTextArea.SaveCancelled)
        return [message for _, message in self.saves if isinstance(message, kinds)]


async def wait_saved(pilot: Pilot[None], area: NovaTextArea) -> None:
    """Wait until no save runs, then let the posted messages arrive."""
    await wait_until(pilot, lambda: not area.saving)
    await pilot.pause(0.05)


def text_of(area: NovaTextArea) -> bytes:
    doc = area.document
    return doc.read_bytes(0, doc.length)


def leftovers(directory: Path) -> list[str]:
    """Names in `directory` that are temp files of a save."""
    return sorted(entry.name for entry in directory.iterdir() if entry.name.endswith(".tmp"))
