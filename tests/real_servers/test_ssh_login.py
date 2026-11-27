"""Smoke tests: the real OpenSSH containers accept a login and report the expected listing mode."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from tests.real_servers.fakes import ConnectFactory
from tests.real_servers.ssh import SshServer

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.ssh]


@pytest.mark.asyncio
async def test_listing_mode_matches_server(ssh_server: SshServer, connect: ConnectFactory) -> None:
    fs = await connect(ssh_server)
    async for _ in fs.home().iterdir():
        pass
    assert fs._listing_via_sftp == (ssh_server.flavour == "busybox")


@pytest.mark.asyncio
async def test_home_directory(ssh_server: SshServer, connect: ConnectFactory) -> None:
    fs = await connect(ssh_server)
    assert fs.home().path == PurePosixPath("/home/nova")
