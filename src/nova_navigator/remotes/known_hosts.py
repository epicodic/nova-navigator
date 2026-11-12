"""Reader for OpenSSH ``known_hosts`` files, independent of Textual and of ``vfs``.

The module deliberately avoids ``paramiko.HostKeys.load``: one ``@cert-authority`` or ``@revoked``
line makes it raise ``InvalidHostKey`` (not an ``SSHException``) and abort the whole load.  Here every
line is parsed on its own, lines that cannot be interpreted are recorded as issues and ignored, and
nothing is ever written back by the reader (REQ-15).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import paramiko
from paramiko.hostkeys import HostKeyEntry, HostKeys, InvalidHostKey

_logger = logging.getLogger(__name__)

SSH_DEFAULT_PORT = 22
_MIN_FIELDS = 3
_HASHED_PREFIX = "|1|"
_PARSE_ERRORS = (InvalidHostKey, paramiko.SSHException, ValueError, binascii.Error)

_KEY_TYPE_NAMES: dict[str, str] = {
    "ssh-rsa": "RSA",
    "ssh-dss": "DSA",
    "ssh-ed25519": "ED25519",
    "ssh-ed448": "ED448",
    "ecdsa-sha2-nistp256": "ECDSA-256",
    "ecdsa-sha2-nistp384": "ECDSA-384",
    "ecdsa-sha2-nistp521": "ECDSA-521",
}

IssueKind = Literal["unreadable", "cert-authority", "unsupported-key-type", "malformed", "unknown-marker"]


@dataclass(frozen=True)
class KnownHostsLocations:
    """Where OpenSSH state lives; injectable so tests never touch the real files."""

    user_known_hosts: Path
    system_known_hosts: Path
    user_config: Path
    system_config: Path

    @classmethod
    def default(cls) -> KnownHostsLocations:
        """Return the real locations (``~`` honours ``$HOME``)."""
        ssh_dir = Path("~/.ssh").expanduser()
        return cls(ssh_dir / "known_hosts", Path("/etc/ssh/ssh_known_hosts"), ssh_dir / "config", Path("/etc/ssh/ssh_config"))

    @classmethod
    def under(cls, home: Path, etc_ssh: Path) -> KnownHostsLocations:
        """Return locations below *home* (``.ssh/``) and *etc_ssh*, for tests."""
        return cls(home / ".ssh" / "known_hosts", etc_ssh / "ssh_known_hosts", home / ".ssh" / "config", etc_ssh / "ssh_config")


@dataclass(frozen=True)
class KnownHostsIssue:
    """A file or line that was ignored."""

    path: Path
    lineno: int | None
    kind: IssueKind
    detail: str


@dataclass(frozen=True)
class KnownHostsEntry:
    """A usable line: its host tokens as written (plain names or one hashed token) and its key."""

    names: tuple[str, ...]
    key: paramiko.PKey


def host_key_name(hostname: str, port: int) -> str:
    """Return the name paramiko and OpenSSH use for a host (``host`` or ``[host]:port``)."""
    return hostname if port == SSH_DEFAULT_PORT else f"[{hostname}]:{port}"


def fingerprint(key: paramiko.PKey) -> str:
    """SHA-256 fingerprint of *key* in OpenSSH display format (``SHA256:...``)."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def key_type_name(key: paramiko.PKey) -> str:
    """Normalised key-type name, e.g. ``'ED25519'`` or ``'RSA'``."""
    return _KEY_TYPE_NAMES.get(key.get_name(), key.get_name().upper())


def _same_key(first: paramiko.PKey, second: paramiko.PKey) -> bool:
    return first.get_name() == second.get_name() and first.asbytes() == second.asbytes()


def _name_matches(token: str, name: str) -> bool:
    if token.startswith(_HASHED_PREFIX):
        try:
            return HostKeys.hash_host(name, token) == token
        except (ValueError, binascii.Error, AssertionError):
            return False
    return token == name


@dataclass
class KnownHosts:
    """Parsed content of one or more known_hosts files."""

    entries: list[KnownHostsEntry] = field(default_factory=list)
    revoked: list[paramiko.PKey] = field(default_factory=list)
    issues: list[KnownHostsIssue] = field(default_factory=list)

    def is_revoked(self, key: paramiko.PKey) -> bool:
        """True if *key* (same type and bytes) is listed on an ``@revoked`` line, for any host."""
        return any(_same_key(key, revoked) for revoked in self.revoked)

    def keys_for(self, name: str) -> list[paramiko.PKey]:
        """Non-revoked keys of the entries whose host tokens match *name* (exact or hashed), in file order."""
        return [e.key for e in self.entries if any(_name_matches(t, name) for t in e.names) and not self.is_revoked(e.key)]

    def apply_to(self, host_keys: paramiko.HostKeys, name: str) -> None:
        """Add the keys for *name* to *host_keys*; the first key per key type wins (user file is read first)."""
        by_type: dict[str, paramiko.PKey] = {}
        for key in self.keys_for(name):
            by_type.setdefault(key.get_name(), key)
        for key_type, key in by_type.items():
            host_keys.add(name, key_type, key)


def _read_line(result: KnownHosts, path: Path, lineno: int, raw: str) -> None:
    fields = raw.split()
    if not fields or fields[0].startswith("#"):
        return

    def ignore(kind: IssueKind, detail: str) -> None:
        result.issues.append(KnownHostsIssue(path, lineno, kind, detail))
        _logger.info("Ignoring known_hosts line %s:%d (%s)", path, lineno, kind)

    marker = None
    if fields[0].startswith("@"):
        marker, fields = fields[0], fields[1:]
        if marker == "@cert-authority":
            ignore("cert-authority", "certificate authority lines are not supported")
            return
        if marker != "@revoked":
            ignore("unknown-marker", f"unknown marker {marker}")
            return
    if len(fields) < _MIN_FIELDS:
        ignore("malformed", "fewer than three fields")
        return
    try:
        entry = HostKeyEntry.from_line(" ".join(fields[:_MIN_FIELDS]), lineno)
    except _PARSE_ERRORS as exc:
        ignore("malformed", type(exc).__name__)
        return
    if entry is None or entry.key is None:
        ignore("unsupported-key-type", fields[1])
        return
    if marker == "@revoked":
        result.revoked.append(entry.key)
    else:
        result.entries.append(KnownHostsEntry(tuple(entry.hostnames), entry.key))


def read_known_hosts(paths: Sequence[Path]) -> KnownHosts:
    """Read *paths* in order (user file first). Never raises; problems are recorded in ``issues``."""
    result = KnownHosts()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.issues.append(KnownHostsIssue(path, None, "unreadable", str(exc)))
            _logger.info("Cannot read known_hosts file %s: %s", path, exc)
            continue
        for lineno, raw in enumerate(text.split("\n"), start=1):
            _read_line(result, path, lineno, raw)
    return result
