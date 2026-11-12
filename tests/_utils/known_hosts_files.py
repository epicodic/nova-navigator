"""Builders for known_hosts test files and an ``ssh-keygen -F`` probe."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import paramiko

KEYGEN = shutil.which("ssh-keygen")
UNSUPPORTED_LINE = "sk-host.example sk-ssh-ed25519@openssh.com AAAAGnNrLXNzaC1lZDI1NTE5QG9wZW5zc2guY29tAAAAIA=="


def make_key(kind: str = "ecdsa") -> paramiko.PKey:
    """Generate a throw-away key (``"ecdsa"`` is fast, ``"rsa"`` uses 1024 bits)."""
    if kind == "rsa":
        return paramiko.RSAKey.generate(bits=1024)
    return paramiko.ECDSAKey.generate()


def key_line(names: str, key: paramiko.PKey, marker: str | None = None, comment: str | None = None) -> str:
    """Return one known_hosts line (no trailing newline)."""
    parts = ([marker] if marker else []) + [names, key.get_name(), key.get_base64()] + ([comment] if comment else [])
    return " ".join(parts)


def mixed_known_hosts(plain_host: str, plain_key: paramiko.PKey, other: paramiko.PKey) -> bytes:
    """A file with every kind of line REQ-13/REQ-15 mention; *plain_host* has a valid plain entry."""
    lines = [
        "# a comment",
        "",
        key_line("*.corp.example", other, marker="@cert-authority"),
        key_line("revoked.example", other, marker="@revoked"),
        key_line(paramiko.HostKeys.hash_host("hashed.example"), other),
        key_line("multi.example,10.0.0.9", other, comment="host,ip entry"),
        UNSUPPORTED_LINE,
        "this line is not a known_hosts entry",
        "bad.example ssh-rsa !!!not-base64!!!",
        "",
        key_line(plain_host, plain_key),
    ]
    return ("\n".join(lines) + "\n").encode()


def keygen_finds(path: Path, name: str) -> bool:
    """True if ``ssh-keygen -F name -f path`` reports a match (requires ssh-keygen)."""
    assert KEYGEN is not None
    result = subprocess.run([KEYGEN, "-F", name, "-f", str(path)], capture_output=True, text=True, check=False)
    return result.returncode == 0 and bool(result.stdout.strip())
