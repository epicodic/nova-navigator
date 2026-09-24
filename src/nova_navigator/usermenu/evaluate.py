"""Compile and evaluate user menu conditions."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import CodeType

from nova_navigator.commands import CommandMode

from .model import MenuConfigError, MenuEntry

_logger = logging.getLogger(__name__)

SAFE_BUILTINS: dict[str, object] = {
    "len": len,
    "any": any,
    "all": all,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "str": str,
    "int": int,
    "bool": bool,
}
"""Builtins available to expressions (hygiene, not a sandbox: the config file is trusted)."""


@dataclass(frozen=True)
class CompiledEntry:
    entry: MenuEntry
    when: CodeType | None
    default: CodeType | bool


@dataclass(frozen=True)
class CompiledMenu:
    """Entries with compiled expressions, plus messages for disabled entries."""

    entries: tuple[CompiledEntry, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class MenuView:
    """The visible entries for one menu open.

    ``rows`` holds entries and ``None`` for separators.
    ``names`` is the context the conditions saw; placeholders use it too.
    """

    rows: tuple[MenuEntry | None, ...]
    default_index: int
    names: Mapping[str, object]

    @property
    def is_empty(self) -> bool:
        return not self.rows

    def entry(self, entry_id: str) -> MenuEntry:
        """Return the visible entry with *entry_id*."""
        return next(row for row in self.rows if row is not None and row.id == entry_id)


def compile_menu(entries: list[MenuEntry]) -> CompiledMenu:
    """Compile all expressions; entries with syntax errors are dropped and reported."""
    compiled: list[CompiledEntry] = []
    errors: list[str] = []
    for entry in entries:
        try:
            when = _compile(entry.id, "when", entry.when) if entry.when is not None else None
            default = _compile(entry.id, "default", entry.default) if isinstance(entry.default, str) else entry.default
        except MenuConfigError as exc:
            errors.append(str(exc))
            continue
        compiled.append(CompiledEntry(entry=entry, when=when, default=default))
    return CompiledMenu(entries=tuple(compiled), errors=tuple(errors))


def evaluate_menu(
    menu: CompiledMenu,
    names: Mapping[str, object],
    scheme: str,
    can_run: Callable[[CommandMode], bool],
) -> MenuView:
    """Return the entries visible for *names* on a filesystem with *scheme*.

    An entry is visible when *scheme* is in its ``on`` list, *can_run* accepts its mode, and ``when`` is true.
    """
    visible: list[tuple[MenuEntry, bool]] = []
    for item in menu.entries:
        entry = item.entry
        if scheme not in entry.on or not can_run(entry.mode):
            continue
        if item.when is not None and not _evaluate(item.when, names, entry.id, "when"):
            continue
        is_default = item.default if isinstance(item.default, bool) else _evaluate(item.default, names, entry.id, "default")
        visible.append((entry, is_default))
    return _layout(visible, names)


def _compile(entry_id: str, field: str, source: str) -> CodeType:
    try:
        return compile(source, f"<usermenu {entry_id}.{field}>", "eval")
    except SyntaxError as exc:
        raise MenuConfigError(f"User menu entry '{entry_id}' disabled: invalid '{field}' expression: {exc.msg}") from exc


def _evaluate(code: CodeType, names: Mapping[str, object], entry_id: str, field: str) -> bool:
    # Context names go into globals (not locals) so generator expressions can see them.
    scope: dict[str, object] = {"__builtins__": SAFE_BUILTINS, **names}
    try:
        return bool(eval(code, scope))
    except Exception:
        _logger.exception("User menu entry '%s': '%s' expression failed", entry_id, field)
        return False


def _layout(visible: list[tuple[MenuEntry, bool]], names: Mapping[str, object]) -> MenuView:
    rows: list[MenuEntry | None] = []
    default_index = -1
    previous_group: str | None = None
    for entry, is_default in visible:
        if rows and entry.group != previous_group:
            rows.append(None)
        previous_group = entry.group
        if is_default and default_index < 0:
            default_index = len(rows)
        rows.append(entry)
    return MenuView(rows=tuple(rows), default_index=max(default_index, 0), names=names)
