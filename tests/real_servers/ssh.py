"""Start the pinned OpenSSH containers once per process and expose them to the tests."""

from __future__ import annotations

import atexit
import contextlib
import functools
import secrets
import shutil
import socket
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.real_servers.docker import ServerUnavailable, build_image, run_docker

LOOPBACK = "127.0.0.1"
FLAVOURS: tuple[Literal["gnu", "busybox"], ...] = ("gnu", "busybox")
READY_DEADLINE = 30.0
SSHD_OPTIONS = "-o AllowTcpForwarding=yes -o PasswordAuthentication=yes"
# OpenSSH 10 (busybox image) enables PerSourcePenalties by default. Every test client comes from one address, so a few
# failed logins would make sshd refuse the later tests. OpenSSH 9.2 (gnu image) does not know the option at all.
SSHD_OPTIONS_BY_FLAVOUR: dict[str, str] = {
    "gnu": SSHD_OPTIONS,
    "busybox": f"{SSHD_OPTIONS} -o PerSourcePenalties=no",
}


@dataclass(frozen=True)
class SshServer:
    """One running OpenSSH container, published on the loopback interface and reachable by alias on the test network."""

    flavour: Literal["gnu", "busybox"]
    container: str
    port: int
    alias: str
    password: str = field(repr=False)
    identity: Path
    home: str = "/home/nova"

    def run(self, script: str, *args: str, input: bytes | None = None) -> bytes:
        """Run *script* as the server user; used to arrange and verify files on the server."""
        return run_docker("exec", "-i", "-u", "nova", self.container, "sh", "-c", script, "sh", *args, input=input)


def _published_port(container: str) -> int:
    lines = run_docker("port", container, "22/tcp").decode().splitlines()
    if not lines:
        raise ServerUnavailable(f"container {container} publishes no port 22")
    return int(lines[0].rsplit(":", 1)[1])


def _wait_for_banner(container: str, port: int) -> None:
    deadline = time.monotonic() + READY_DEADLINE
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((LOOPBACK, port), timeout=2) as sock:
                if sock.recv(4).startswith(b"SSH-"):
                    return
        except OSError:
            pass
        time.sleep(0.2)
    logs = run_docker("logs", container).decode(errors="replace")
    raise ServerUnavailable(f"sshd in {container} did not greet within {READY_DEADLINE:g} s: {logs[-500:]}")


def _start(flavour: Literal["gnu", "busybox"], network: str, identity: Path, public_key: str) -> SshServer:
    build_image(flavour)
    container = f"nova-rs-{flavour}-{secrets.token_hex(4)}"
    run_docker(
        "run",
        "-d",
        "--rm",
        "--name",
        container,
        "--network",
        network,
        "--network-alias",
        flavour,
        "-p",
        f"{LOOPBACK}::22",
        f"nova-rs-{flavour}",
        "sh",
        "-c",
        f"ssh-keygen -A && exec timeout 1800 /usr/sbin/sshd -D -e {SSHD_OPTIONS_BY_FLAVOUR[flavour]}",
    )
    atexit.register(_remove_container, container)
    password = secrets.token_urlsafe(16)
    run_docker("exec", "-i", container, "chpasswd", input=f"nova:{password}\n".encode())
    run_docker(
        "exec",
        "-i",
        "-u",
        "nova",
        container,
        "sh",
        "-c",
        "mkdir -p ~/.ssh && cat > ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys",
        input=public_key.encode(),
    )
    port = _published_port(container)
    _wait_for_banner(container, port)
    return SshServer(flavour=flavour, container=container, port=port, alias=flavour, password=password, identity=identity)


@functools.cache
def ssh_servers() -> dict[str, SshServer]:
    """Start the gnu and busybox servers on one network, once per process; everything is removed at exit."""
    workdir = Path(tempfile.mkdtemp(prefix="nova-rs-keys-"))
    atexit.register(shutil.rmtree, workdir, True)
    identity = workdir / "id_ed25519"
    key = Ed25519PrivateKey.generate()
    identity.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
    identity.chmod(0o600)
    public_key = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()

    network = f"nova-rs-net-{secrets.token_hex(4)}"
    run_docker("network", "create", network)
    # atexit runs last-in first-out: containers are removed before the network they are attached to.
    atexit.register(_remove_network, network)
    return {flavour: _start(flavour, network, identity, public_key) for flavour in FLAVOURS}


def _remove_container(name: str) -> None:
    """Force-remove a container at exit; a failure must not hide the test result."""
    with contextlib.suppress(ServerUnavailable):
        run_docker("rm", "-f", name)


def _remove_network(name: str) -> None:
    """Remove a network at exit; a failure must not hide the test result."""
    with contextlib.suppress(ServerUnavailable):
        run_docker("network", "rm", name)
