import logging
import subprocess
import sys
import traceback
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import debugpy

_logger = logging.getLogger(__name__)


def launch_debugger(project_dir: str | None = None) -> None:
    """Launch debugpy and open VS Code in the project directory."""
    _project_dir = project_dir or str(Path.cwd())
    try:
        port = 5678
        _logger.info("Starting debugpy on port %d, opening VS Code...", port)

        debugpy.listen(("localhost", port))

        # Open VS Code in the project directory
        subprocess.Popen(["code", _project_dir])

        _logger.info("Waiting for debugger to attach at localhost:%d ...", port)
        _logger.info("In VS Code: Run > Start Debugging, select 'Attach (post-mortem)'")

        debugpy.wait_for_client()
        debugpy.breakpoint()  # Pause here — stack is still inspectable above

    except Exception as e:  # noqa: BLE001
        _logger.error("Failed to start debugger", exc_info=e)


_debugpy_armed = False


def trace_handler(_frame: types.FrameType, event: str, _arg: Any) -> Callable[[types.FrameType, str, Any], Any] | None:
    global _debugpy_armed  # noqa: PLW0603
    if event == "exception" and not _debugpy_armed:
        _debugpy_armed = True
        launch_debugger()
        # _debugpy_armed = False  # Reset for next time

    return trace_handler


def exception_handler(
    exc_type: type[BaseException], exc_value: BaseException, exc_tb: types.TracebackType | None
) -> None:
    traceback.print_exception(exc_type, exc_value, exc_tb)
    launch_debugger()


def install_debug_handler() -> None:
    sys.excepthook = exception_handler
