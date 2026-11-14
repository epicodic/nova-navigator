"""Resolve a requested SSH host into the ordered hops (jump hosts, then the target); independent of Textual and of ``vfs``."""

from __future__ import annotations

import getpass
import logging
from dataclasses import dataclass

from nova_navigator.remotes.ssh_config import SshConfig, SshOptions

_logger = logging.getLogger(__name__)

MAX_HOPS = 8
_DEFAULT_PORT = 22
_SSH_URI_PREFIX = "ssh://"


class SshRouteError(Exception):
    """The ProxyJump chain cannot be built (loop or too long); the message names the host involved."""


@dataclass(frozen=True)
class SshHop:
    """One connection of the route.  *alias* is the name as written (config lookup key and display), *hostname* the resolved ``HostName``."""

    alias: str
    hostname: str
    port: int
    user: str | None
    identity_files: tuple[str, ...]


@dataclass(frozen=True)
class Explicit:
    """Values the user gave explicitly for the target (saved remote or ``ssh://`` URI); they always win."""

    user: str | None = None
    port: int | None = None
    identity_file: str | None = None


def _local_user() -> str | None:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return None


def _split_list(value: str | None) -> list[str]:
    if value is None or value.strip().lower() == "none":
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_jump(spec: str) -> tuple[str | None, str, int | None]:
    """Parse ``[ssh://][user@]host[:port]`` (``host`` may be ``[v6]``) into ``(user, host, port)``."""
    rest = spec.removeprefix(_SSH_URI_PREFIX)
    user: str | None = None
    if "@" in rest:
        user_part, rest = rest.rsplit("@", 1)
        user = user_part or None
    port: int | None = None
    if rest.startswith("[") and "]" in rest:
        host, _, tail = rest[1:].partition("]")
        if tail.startswith(":") and tail[1:].isdigit():
            port = int(tail[1:])
    elif rest.count(":") == 1:
        host, _, port_text = rest.partition(":")
        port = int(port_text) if port_text.isdigit() else None
    else:
        host = rest
    return user, host, port


def _hop(alias: str, options: SshOptions, *, user: str | None = None, port: int | None = None, identity_files: tuple[str, ...] | None = None) -> SshHop:
    return SshHop(
        alias=alias,
        hostname=options.hostname,
        port=port or options.port or _DEFAULT_PORT,
        user=user or options.user or _local_user(),
        identity_files=options.identity_files if identity_files is None else identity_files,
    )


def _jumps(alias: str, options: SshOptions, config: SshConfig, path: tuple[str, ...]) -> list[SshHop]:
    """Return the hops that lead to *alias* (excluding *alias* itself), each jump's own ProxyJump applied first."""
    if alias in path:
        raise SshRouteError(f"ProxyJump loop through {alias!r}: {' -> '.join((*path, alias))}")
    if len(path) >= MAX_HOPS:
        raise SshRouteError(f"ProxyJump chain for {alias!r} has more than {MAX_HOPS} hops")
    if options.proxy_command_set:
        _logger.info("ProxyCommand is not supported and is ignored for %r", alias)
    hops: list[SshHop] = []
    for spec in _split_list(options.proxy_jump):
        user, host, port = _parse_jump(spec)
        jump_options = config.lookup(host)
        hops.extend(_jumps(host, jump_options, config, (*path, alias)))
        hops.append(_hop(host, jump_options, user=user, port=port))
    return hops


def resolve_route(host: str, explicit: Explicit, config: SshConfig) -> list[SshHop]:
    """Return ``[jump..., target]`` for *host*; never empty, the target is last.

    Raises:
        SshRouteError: The ProxyJump chain loops or is longer than ``MAX_HOPS`` hops in total.
    """
    options = config.lookup(host)
    jumps = _jumps(host, options, config, ())
    if len(jumps) + 1 > MAX_HOPS:
        raise SshRouteError(f"ProxyJump chain for {host!r} has more than {MAX_HOPS} hops")
    identity = (explicit.identity_file,) if explicit.identity_file else None
    return [*jumps, _hop(host, options, user=explicit.user, port=explicit.port, identity_files=identity)]
