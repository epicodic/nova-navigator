from dataclasses import dataclass


@dataclass
class Stat:
    """A class representing the stats of a path."""

    size: int = -1
    modified: float = -1.0
    mode: int = -1
    is_hidden: bool = False
    is_directory: bool = False
    is_executable: bool = False
    is_symlink: bool = False
    is_broken_symlink: bool = False


OUTPUT_TAIL_LIMIT = 64 * 1024
"""Maximum number of output bytes kept by ``Filesystem.exec_command``."""


@dataclass(frozen=True)
class ExecResult:
    """Outcome of ``Filesystem.exec_command``."""

    exit_code: int
    """Process exit code; ``-1`` when the command was cancelled."""

    output: str
    """Tail of the merged stdout and stderr, decoded as UTF-8."""
