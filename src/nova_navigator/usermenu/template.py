"""Placeholder expansion for user menu scripts and input defaults."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping

_PLACEHOLDER = re.compile(r"(?<!\$)\{([A-Za-z_]\w*(?:\.\w+)*)(:raw)?\}")


class PlaceholderError(Exception):
    """A placeholder cannot be expanded."""


def expand(template: str, names: Mapping[str, object], *, quote: bool = True) -> str:
    """Replace ``{name}`` / ``{name.attr}`` / ``{name:raw}`` placeholders in *template*.

    Values are shell-quoted unless *quote* is False or ``:raw`` is given.
    Lists expand to space-separated items; an attribute on a list maps over its items.
    ``${...}`` and placeholders whose first name is not in *names* are left unchanged.

    Raises:
        PlaceholderError: An attribute is missing or private, or the value is None or callable.

    Note:
        Do not wrap a placeholder in quotes (``'{file}'`` or ``"{file}"``) — ``{name}`` already
        expands to a safely quoted shell token, and manual quoting splits arguments or breaks the
        script. Use ``{name:raw}`` only when building your own quoting.
        Quoting does not stop a value that starts with ``-`` from being read as an option by the
        called program; scripts should put ``--`` before file arguments, or prefer ``{file}`` over
        ``{file.name}`` so the value is an absolute path.
    """

    def _replace(match: re.Match[str]) -> str:
        parts = match.group(1).split(".")
        if parts[0] not in names:
            return match.group(0)
        placeholder = match.group(0)
        value = _resolve(names[parts[0]], parts[1:], placeholder)
        return _format(value, raw=match.group(2) is not None or not quote, placeholder=placeholder)

    return _PLACEHOLDER.sub(_replace, template)


def _resolve(value: object, attrs: list[str], placeholder: str) -> object:
    for attr in attrs:
        if attr.startswith("_"):
            raise PlaceholderError(f"{placeholder}: private attribute '{attr}'")
        if isinstance(value, list | tuple):
            value = [_getattr(item, attr, placeholder) for item in value]
        else:
            value = _getattr(value, attr, placeholder)
    return value


def _getattr(obj: object, attr: str, placeholder: str) -> object:
    try:
        return getattr(obj, attr)
    except AttributeError:
        raise PlaceholderError(f"{placeholder}: no attribute '{attr}'") from None


def _format(value: object, *, raw: bool, placeholder: str) -> str:
    items = value if isinstance(value, list | tuple) else [value]
    parts = [_scalar(item, placeholder) for item in items]
    if not raw:
        parts = [shlex.quote(part) for part in parts]
    return " ".join(parts)


def _scalar(value: object, placeholder: str) -> str:
    if value is None:
        raise PlaceholderError(f"{placeholder} has no value")
    if callable(value):
        raise PlaceholderError(f"{placeholder} is not a value")
    return str(value)
