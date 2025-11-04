from dataclasses import dataclass


@dataclass
class PathStats:
    """A class representing the stats of a path."""

    size: int = -1
    modified: float = -1.0
    is_hidden: bool = False
    is_directory: bool = False
    is_executable: bool = False
    is_symlink: bool = False
    is_broken_symlink: bool = False
