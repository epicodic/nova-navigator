"""In-memory registry of the passwords the user gave, and the scrubber for diagnostic output.

Every new way of receiving a password or passphrase must register it here.
Registered values live as long as the process and are never written anywhere.
"""

from __future__ import annotations

import re
import threading

REDACTED = "<redacted>"

_URI_USERINFO = re.compile(r"://([^/]*)@")

_lock = threading.Lock()
_registry: dict[str, frozenset[str]] = {"forms": frozenset()}


def _escape_str(text: str) -> str:
    """Return *text* as it appears between the quotes of a ``str`` repr."""
    return "".join(repr(char)[1:-1] for char in text)


def _escape_bytes(text: str) -> str:
    """Return *text* as it appears between the quotes of a ``bytes`` repr."""
    return "".join(repr(bytes([byte]))[2:-1] for byte in text.encode("utf-8", "surrogatepass"))


def _with_quote_variants(forms: set[str]) -> set[str]:
    """Add the spelling in which ``'`` is escaped, used when the surrounding text holds both quote kinds."""
    return forms | {form.replace("'", "\\'") for form in forms}


def _forms_of(secret: str) -> set[str]:
    once = _with_quote_variants({_escape_str(secret), _escape_bytes(secret), "".join(ascii(char)[1:-1] for char in secret)})
    twice = _with_quote_variants({_escape_str(form) for form in once})
    return {secret} | once | twice


def register_secret(secret: str | None) -> None:
    """Remember a password or passphrase so that :func:`scrub` removes it. Empty values are ignored."""
    if not secret:
        return
    with _lock:
        _registry["forms"] = _registry["forms"] | _forms_of(secret)


def register_userinfo(userinfo: str | None) -> None:
    """Register the password of a ``user:password`` string, if it has one."""
    if userinfo and ":" in userinfo:
        register_secret(userinfo.split(":", 1)[1])


def register_uri_secrets(uri: str) -> None:
    """Register the password of every ``scheme://user:password@host`` part of *uri*."""
    for match in _URI_USERINFO.finditer(uri):
        register_userinfo(match.group(1))


def scrub(text: str) -> str:
    """Return *text* with every registered secret, in any of its forms, replaced by ``REDACTED``."""
    spans: list[tuple[int, int]] = []
    for form in _registry["forms"]:
        start = text.find(form)
        while start != -1:
            spans.append((start, start + len(form)))
            start = text.find(form, start + 1)
    if not spans:
        return text
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start < last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    pieces: list[str] = []
    position = 0
    for start, end in merged:
        pieces.append(text[position:start])
        pieces.append(REDACTED)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def clear_secrets() -> None:
    """Forget all registered secrets. For test isolation only."""
    with _lock:
        _registry["forms"] = frozenset()
