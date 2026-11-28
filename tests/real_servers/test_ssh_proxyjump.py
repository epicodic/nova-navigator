"""ProxyJump between the two real OpenSSH servers: the jump is one flavour, the target is reached through it."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import copy_files
from nova_navigator.remotes.ssh import UNKNOWN_TITLE
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests.filemanager.common import run_task
from tests.real_servers.fakes import ConnectFactory, FakeMessageBox
from tests.real_servers.ssh import SshServer
from tests.real_servers.test_ssh_transfer import PAYLOAD, _arrange, _local, _remote_tree, _tree_of_dir, _write_local_payload

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.ssh]


def _write_config(home: Path, jump: SshServer, target: SshServer) -> None:
    """An ssh config in the isolated home: *jump* is reached on its published port, *target* through it by name."""
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    (ssh_dir / "config").write_text(
        f"Host jump-hop\n"
        f"  HostName 127.0.0.1\n"
        f"  Port {jump.port}\n"
        f"  User nova\n"
        f"  IdentityFile {jump.identity}\n"
        f"Host target-hop\n"
        f"  HostName {target.alias}\n"
        f"  Port 22\n"
        f"  User nova\n"
        f"  ProxyJump jump-hop\n"
        f"  IdentityFile {target.identity}\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("jump", "target"), [("busybox", "gnu"), ("gnu", "busybox")])
async def test_proxyjump_list_and_copy(
    ssh_pair: dict[str, SshServer],
    isolated: Path,
    connect: ConnectFactory,
    tmp_path: Path,
    jump: str,
    target: str,
) -> None:
    jump_server, target_server = ssh_pair[jump], ssh_pair[target]
    _write_config(isolated, jump_server, target_server)
    base = f"{target_server.home}/hop-{target}"
    target_server.run('mkdir -p "$1"', base)
    _arrange(target_server, base)

    fs: SSHFilesystem = await connect(target_server, host="target-hop", port=22)
    assert [box.title for box in FakeMessageBox.shown] == [UNKNOWN_TITLE, UNKNOWN_TITLE]

    names = [vp.name async for vp in VPath(base, fs).iterdir()]
    assert names == [PAYLOAD]

    dst = tmp_path / "dst"
    dst.mkdir()
    await run_task(lambda ctx: copy_files(ctx, [VPath(f"{base}/{PAYLOAD}", fs)], _local(dst)))
    assert _tree_of_dir(dst / PAYLOAD) == _remote_tree(target_server, base)

    src = tmp_path / "src"
    src.mkdir()
    _write_local_payload(src)
    upload = f"{base}/upload"
    target_server.run('mkdir -p "$1"', upload)
    await run_task(lambda ctx: copy_files(ctx, [_local(src / PAYLOAD)], VPath(upload, fs)))
    assert _remote_tree(target_server, upload) == _tree_of_dir(src / PAYLOAD)
