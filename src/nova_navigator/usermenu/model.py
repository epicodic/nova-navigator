"""User menu config model and parser."""

from __future__ import annotations

import keyword
import tomllib
from dataclasses import dataclass
from typing import Any

from nova_navigator.commands import CommandMode

from .context import CONTEXT_NAMES


class MenuConfigError(Exception):
    """Raised when the user menu file cannot be parsed or fails validation."""


@dataclass(frozen=True)
class MenuInput:
    """A value asked from the user before an entry runs."""

    name: str
    prompt: str
    default: str = ""


@dataclass(frozen=True)
class MenuEntry:
    """One user menu entry, as written in the config file."""

    id: str
    label: str
    run: str
    key: str | None = None
    group: str | None = None
    on: tuple[str, ...] = ("local",)
    when: str | None = None
    default: str | bool = False
    mode: CommandMode = CommandMode.TERMINAL
    inputs: tuple[MenuInput, ...] = ()


_ENTRY_FIELDS = frozenset({"key", "label", "group", "on", "when", "default", "mode", "run", "input"})
_INPUT_FIELDS = frozenset({"name", "prompt", "default"})


def parse_menu(text: str) -> list[MenuEntry]:
    """Parse the TOML *text* of a user menu file, in file order.

    Raises:
        MenuConfigError: The text is not valid TOML or an entry is invalid.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MenuConfigError(str(exc)) from exc
    return [_parse_entry(entry_id, table) for entry_id, table in data.items()]


def _parse_entry(entry_id: str, table: object) -> MenuEntry:
    where = f"entry '{entry_id}'"
    if not isinstance(table, dict):
        raise MenuConfigError(f"{where}: must be a table")
    _check_fields(table, _ENTRY_FIELDS, where)
    key = _opt_str(table, "key", where)
    if key is not None and len(key) != 1:
        raise MenuConfigError(f"{where}: 'key' must be a single character")
    mode_value = _opt_str(table, "mode", where) or CommandMode.TERMINAL.value
    try:
        mode = CommandMode(mode_value)
    except ValueError:
        raise MenuConfigError(f"{where}: 'mode' must be 'terminal' or 'background'") from None
    default = _get(table, "default", False)
    if not isinstance(default, str | bool):
        raise MenuConfigError(f"{where}: 'default' must be a string or a boolean")
    return MenuEntry(
        id=entry_id,
        label=_req_str(table, "label", where),
        run=_req_str(table, "run", where),
        key=key,
        group=_opt_str(table, "group", where),
        on=_str_tuple(table, "on", where, fallback=("local",)),
        when=_opt_str(table, "when", where),
        default=default,
        mode=mode,
        inputs=_parse_inputs(_get(table, "input", []), where),
    )


def _parse_inputs(raw: object, where: str) -> tuple[MenuInput, ...]:
    if not isinstance(raw, list):
        raise MenuConfigError(f"{where}: 'input' must be an array of tables")
    inputs: list[MenuInput] = []
    for item in raw:
        if not isinstance(item, dict):
            raise MenuConfigError(f"{where}: 'input' must be an array of tables")
        _check_fields(item, _INPUT_FIELDS, f"{where} input")
        name = _req_str(item, "name", f"{where} input")
        if not name.isidentifier() or keyword.iskeyword(name):
            raise MenuConfigError(f"{where}: input name '{name}' must be a Python identifier")
        if name in CONTEXT_NAMES:
            raise MenuConfigError(f"{where}: input name '{name}' is reserved")
        if any(existing.name == name for existing in inputs):
            raise MenuConfigError(f"{where}: duplicate input name '{name}'")
        prompt = _req_str(item, "prompt", f"{where} input")
        inputs.append(MenuInput(name=name, prompt=prompt, default=_opt_str(item, "default", f"{where} input") or ""))
    return tuple(inputs)


def _get(table: dict[Any, Any], field: str, fallback: object) -> object:
    return table.get(field, fallback)


def _check_fields(table: dict[Any, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise MenuConfigError(f"{where}: unknown field '{unknown[0]}'")


def _req_str(table: dict[Any, Any], field: str, where: str) -> str:
    value = _opt_str(table, field, where)
    if value is None:
        raise MenuConfigError(f"{where}: missing required field '{field}'")
    return value


def _opt_str(table: dict[Any, Any], field: str, where: str) -> str | None:
    value = table.get(field)
    if value is not None and not isinstance(value, str):
        raise MenuConfigError(f"{where}: '{field}' must be a string")
    return value


def _str_tuple(table: dict[Any, Any], field: str, where: str, *, fallback: tuple[str, ...]) -> tuple[str, ...]:
    value = table.get(field)
    if value is None:
        return fallback
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise MenuConfigError(f"{where}: '{field}' must be an array of strings")
    return tuple(value)
