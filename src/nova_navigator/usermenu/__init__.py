"""User menu — config, condition evaluation and placeholder expansion (no Textual imports)."""

from .context import CONTEXT_NAMES, DirInfo, FileInfo, PanelInfo, PanelSnapshot, Probe, build_context
from .evaluate import CompiledMenu, MenuView, compile_menu, evaluate_menu
from .model import MenuConfigError, MenuEntry, MenuInput, parse_menu
from .store import DEFAULT_MENU_PATH, USER_MENU_FILENAME, LoadResult, UserMenuStore
from .template import PlaceholderError, expand

__all__ = [
    "CONTEXT_NAMES",
    "DEFAULT_MENU_PATH",
    "USER_MENU_FILENAME",
    "CompiledMenu",
    "DirInfo",
    "FileInfo",
    "LoadResult",
    "MenuConfigError",
    "MenuEntry",
    "MenuInput",
    "MenuView",
    "PanelInfo",
    "PanelSnapshot",
    "PlaceholderError",
    "Probe",
    "UserMenuStore",
    "build_context",
    "compile_menu",
    "evaluate_menu",
    "expand",
    "parse_menu",
]
