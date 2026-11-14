"""Loader for OpenSSH client configuration, independent of Textual and of ``vfs``.

Both files (``~/.ssh/config``, then ``/etc/ssh/ssh_config``) are read once per connection.
``Match`` lines that could run a command are neutralised before parsing, ``Include`` is not followed,
and nothing here ever starts a process.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import paramiko
from paramiko.ssh_exception import ConfigParseError

_logger = logging.getLogger(__name__)

_MIN_PORT = 1
_MAX_PORT = 65535


_CONFIG_ERRORS = (OSError, ConfigParseError, ValueError)


_MATCH_KEYWORDS_WITH_ARGUMENT = frozenset({"host", "originalhost", "user", "localuser"})
_MATCH_KEYWORDS_WITHOUT_ARGUMENT = frozenset({"all", "canonical", "final"})


def _is_safe_match_line(line: str) -> bool:
    """Return True if *line* is a ``Match`` line made only of keywords that never run a command.

    Paramiko reads the line as ``key[=| ]value`` and then runs ``shlex.split`` on the value, so quoting and escaping
    (``e"xec"``, ``exe''c``, backslash escapes) still yield the keyword ``exec``.  A substring check therefore cannot work;
    this is an allowlist built on the same ``shlex`` tokenisation.  Anything unknown, a keyword without its
    argument, a tokenisation error or ``Match=...`` (whose first token is not ``match``) is not safe.
    """
    try:
        tokens = shlex.split(line.strip())
    except ValueError:
        return False
    if not tokens or tokens[0].lower() != "match":
        return False
    rest = iter(tokens[1:])
    for token in rest:
        keyword = token.lower().removeprefix("!")
        if keyword in _MATCH_KEYWORDS_WITH_ARGUMENT:
            if next(rest, None) is None:
                return False
        elif keyword not in _MATCH_KEYWORDS_WITHOUT_ARGUMENT:
            return False
    return True


def _neutralise_match_exec(text: str) -> str:
    """Replace every ``Match`` line that is not made of known-safe keywords by ``Host !*``, keeping the line count.

    Only ``host``, ``originalhost``, ``user``, ``localuser``, ``all``, ``canonical`` and ``final`` are kept; in
    particular ``exec`` (which paramiko runs through a shell) never survives, however it is quoted.  Lines are split
    on newline characters only and judged with ``str.strip()``, exactly like paramiko does, so no leading Unicode
    whitespace can hide a command from this check.
    """
    lines = []
    for line in text.split("\n"):
        is_match = line.strip().lower().startswith("match")
        lines.append("Host !*" if is_match and not _is_safe_match_line(line) else line)
    return "\n".join(lines)


@dataclass(frozen=True)
class SshOptions:
    """The effective options for one host name; unset values are ``None`` / empty."""

    hostname: str
    port: int | None = None
    user: str | None = None
    identity_files: tuple[str, ...] = ()
    proxy_jump: str | None = None
    proxy_command_set: bool = False
    hash_known_hosts: bool = False


def _first_value(found: Sequence[paramiko.SSHConfigDict], key: str) -> str | None:
    for options in found:
        value = options.get(key)
        if value is not None:
            return str(value)
    return None


def _port(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError:
        _logger.info("Ignoring ssh_config Port value that is not a number")
        return None
    if not _MIN_PORT <= number <= _MAX_PORT:
        _logger.info("Ignoring ssh_config Port value out of range")
        return None
    return number


class SshConfig:
    """Parsed ssh_config files in precedence order (user file first); the first value of a keyword wins."""

    def __init__(self, parsed: Sequence[paramiko.SSHConfig] = ()) -> None:
        self._parsed = tuple(parsed)

    @classmethod
    def load(cls, paths: Sequence[Path]) -> SshConfig:
        """Read *paths* in order.  A missing file is skipped silently, an unreadable or unparseable one is logged by exception type and skipped."""
        parsed: list[paramiko.SSHConfig] = []
        for path in paths:
            try:
                text = _neutralise_match_exec(path.read_text(encoding="utf-8", errors="replace"))
                parsed.append(paramiko.SSHConfig.from_text(text))
            except FileNotFoundError:
                continue
            except _CONFIG_ERRORS as exc:
                _logger.info("Ignoring ssh_config %s: %s", path, type(exc).__name__)
        return cls(parsed)

    def lookup(self, host: str) -> SshOptions:
        """Return the merged options for *host* (the name as requested, e.g. an alias)."""
        found: list[paramiko.SSHConfigDict] = []
        for config in self._parsed:
            try:
                found.append(config.lookup(host))
            except _CONFIG_ERRORS as exc:
                _logger.info("Ignoring ssh_config lookup for a host: %s", type(exc).__name__)
        hostname = next((str(o["hostname"]) for o in found if o.get("hostname") not in (None, host)), host)
        identity: list[str] = []
        for options in found:
            identity.extend(item for item in options.get("identityfile", []) if item not in identity)
        proxy_command_set = False
        for options in found:
            if "proxycommand" in options:  # paramiko stores ``ProxyCommand none`` as None
                proxy_command_set = options["proxycommand"] is not None
                break
        hashed = _first_value(found, "hashknownhosts")
        return SshOptions(
            hostname=hostname,
            port=_port(_first_value(found, "port")),
            user=_first_value(found, "user"),
            identity_files=tuple(identity),
            proxy_jump=_first_value(found, "proxyjump"),
            proxy_command_set=proxy_command_set,
            hash_known_hosts=hashed is not None and hashed.strip().lower() == "yes",
        )
