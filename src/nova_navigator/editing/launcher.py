"""Safe local process launching for editing mirrors."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from .model import EditingSession


def expand_command(command: list[str], mirror: Path) -> list[str]:
    """Expand ``%f`` and ``%d`` placeholders without invoking a shell."""
    return [argument.replace("%f", str(mirror)).replace("%d", str(mirror.parent)) for argument in command]


class EditingLauncher:
    """Launch external editors from the local mirror directory."""

    def __init__(self, popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen) -> None:
        self._popen = popen

    def launch(self, session: EditingSession, command: list[str]) -> subprocess.Popen[bytes]:
        """Start the configured command without a shell."""
        return self._popen(expand_command(command, session.mirror_path), cwd=session.mirror_path.parent)

    def launch_waiting(
        self,
        session: EditingSession,
        command: list[str],
        finish: Callable[[str], object],
    ) -> subprocess.Popen[bytes]:
        """Start a configured waiting editor and finish after successful exit."""
        process = self.launch(session, command)
        if process.wait() == 0:
            finish(session.session_id)
        return process
