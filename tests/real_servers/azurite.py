"""Start the pinned Azurite container once per process and expose it to the Azure tests.

The container serves HTTPS with a self-signed certificate. A fake ``az`` on ``PATH`` prints an unsigned token, so
``DefaultAzureCredential`` signs in through the Azure CLI credential without any account.
"""

from __future__ import annotations

import atexit
import base64
import contextlib
import datetime
import http.client
import ipaddress
import json
import os
import secrets
import shutil
import ssl
import stat
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from tests.real_servers.docker import AZURITE_IMAGE, ServerUnavailable, pull_azurite, run_docker, start_once, unavailable

LOOPBACK = "127.0.0.1"
IMDS_ADDRESS = "169.254.169.254"
ACCOUNT = "devstoreaccount1"
# The well-known development key of every Azurite install; it is not a secret.
ACCOUNT_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
CONTAINER_PORT = 10000
READY_DEADLINE = 30.0
TENANT = str(uuid.UUID("11111111-2222-3333-4444-555555555555"))
OBJECT_ID = str(uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa"))
# The image runs as root; /etc keeps the copies away from the shared temporary directory.
CERT_IN_CONTAINER = "/etc/nova-azurite-cert.pem"
KEY_IN_CONTAINER = "/etc/nova-azurite-key.pem"
# Every interface inside the container, so that the published loopback port reaches Azurite.
ALL_INTERFACES = str(ipaddress.IPv4Address(0))


@dataclass(frozen=True)
class Azurite:
    """One running Azurite container, published on the loopback interface."""

    container: str
    port: int
    cert: Path  # self-signed; also REQUESTS_CA_BUNDLE for the SDK
    bin_dir: Path  # holds the fake `az`

    @property
    def path_style_url(self) -> str:
        return f"https://{LOOPBACK}:{self.port}/{ACCOUNT}"

    @property
    def host_style_url(self) -> str:
        return f"https://{ACCOUNT}.blob.localhost:{self.port}"

    def environment(self, home: Path) -> dict[str, str]:
        """The environment variables that make the SDK reach this Azurite with the fake `az` as its credential."""
        return {
            "PATH": f"{self.bin_dir}:{_inherited_path()}",
            "HOME": str(home),
            "REQUESTS_CA_BUNDLE": str(self.cert),
            # The IMDS address must bypass a proxy: DefaultAzureCredential probes it first, and a proxied probe
            # retries for about 90 s before it gives up.
            "NO_PROXY": f"127.0.0.1,localhost,.localhost,{IMDS_ADDRESS}",
            "AZURE_TOKEN_CREDENTIALS": "AzureCliCredential",
        }


def _inherited_path() -> str:
    return os.environ.get("PATH", "")


def _write_certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName(f"{ACCOUNT}.blob.localhost"),
                    x509.IPAddress(ipaddress.ip_address(LOOPBACK)),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


def _b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _write_fake_az(bin_dir: Path) -> None:
    """Write an executable `az` that prints an unsigned token for any arguments."""
    now = int(time.time())
    claims: dict[str, object] = {
        "aud": "https://storage.azure.com",
        "iss": f"https://sts.windows.net/{TENANT}/",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "tid": TENANT,
        "oid": OBJECT_ID,
    }
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}."
    expires_local = datetime.datetime.fromtimestamp(now + 3600).strftime("%Y-%m-%d %H:%M:%S.%f")
    output = json.dumps(
        {
            "accessToken": token,
            "expiresOn": expires_local,
            "expires_on": now + 3600,
            "tokenType": "Bearer",
        }
    )
    script = bin_dir / "az"
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({output!r})\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _published_port(container: str) -> int:
    lines = run_docker("port", container, f"{CONTAINER_PORT}/tcp").decode().splitlines()
    if not lines:
        raise ServerUnavailable(f"container {container} publishes no port {CONTAINER_PORT}")
    return int(lines[0].rsplit(":", 1)[1])


def _wait_ready(container: str, port: int, cert: Path) -> None:
    """Wait until the HTTPS endpoint answers with any HTTP status."""
    context = ssl.create_default_context(cafile=str(cert))
    deadline = time.monotonic() + READY_DEADLINE
    while time.monotonic() < deadline:
        try:
            connection = http.client.HTTPSConnection(LOOPBACK, port, timeout=2, context=context)
            try:
                connection.request("GET", f"/{ACCOUNT}")
                connection.getresponse().read()
                return
            finally:
                connection.close()
        except (OSError, http.client.HTTPException):
            time.sleep(0.2)
    logs = run_docker("logs", container).decode(errors="replace")
    raise ServerUnavailable(f"Azurite in {container} did not answer within {READY_DEADLINE:g} s: {logs[-500:]}")


def _remove_container(name: str) -> None:
    """Force-remove the container at exit; a failure must not hide the test result."""
    with contextlib.suppress(ServerUnavailable):
        run_docker("rm", "-f", name)


@start_once
def azurite_server() -> Azurite:
    """Start Azurite once per process; the container and the scratch files are removed at exit."""
    pull_azurite()
    workdir = Path(tempfile.mkdtemp(prefix="nova-rs-azurite-"))
    atexit.register(shutil.rmtree, workdir, True)
    cert, key = _write_certificate(workdir)
    bin_dir = workdir / "bin"
    bin_dir.mkdir()
    _write_fake_az(bin_dir)
    container = f"nova-rs-azurite-{secrets.token_hex(4)}"
    run_docker(
        "create",
        "--rm",
        "--name",
        container,
        "-p",
        f"{LOOPBACK}::{CONTAINER_PORT}",
        AZURITE_IMAGE,
        "timeout",
        "1800",
        "azurite-blob",
        "--blobHost",
        ALL_INTERFACES,
        "--blobPort",
        str(CONTAINER_PORT),
        "--oauth",
        "basic",
        "--cert",
        CERT_IN_CONTAINER,
        "--key",
        KEY_IN_CONTAINER,
        "--skipApiVersionCheck",
    )
    atexit.register(_remove_container, container)
    run_docker("cp", str(cert), f"{container}:{CERT_IN_CONTAINER}")
    run_docker("cp", str(key), f"{container}:{KEY_IN_CONTAINER}")
    run_docker("start", container)
    port = _published_port(container)
    _wait_ready(container, port, cert)
    return Azurite(container=container, port=port, cert=cert, bin_dir=bin_dir)


def azurite_or_unavailable() -> Azurite:
    """Return the shared Azurite, or fail or skip the calling test through `unavailable`."""
    try:
        return azurite_server()
    except ServerUnavailable as exc:
        unavailable(str(exc), os.environ)
