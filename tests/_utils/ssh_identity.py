"""Write throw-away client identities for tests."""

from __future__ import annotations

from pathlib import Path

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def write_identity(path: Path) -> tuple[str, str]:
    """Write an OpenSSH-format Ed25519 private key to *path*; return ``(path, base64 public blob)``."""
    private = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    path.write_bytes(private)
    path.chmod(0o600)
    return str(path), paramiko.Ed25519Key.from_private_key_file(str(path)).get_base64()
