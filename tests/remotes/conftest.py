"""Isolate the tests in this directory from the real home directory and SSH agent; fixtures of the connect flow."""

from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nova_navigator.config import conf_
from nova_navigator.config.remotes import RemoteConnection, SshSettings
from nova_navigator.config.settings import Settings
from nova_navigator.dialogs.credentials_dialog import Credentials
from nova_navigator.remotes import ssh as ssh_module
from nova_navigator.remotes.known_hosts import KnownHostsLocations, host_key_name
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_widgets import Response
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``$HOME`` at an empty directory and drop the SSH agent socket."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    return home


@pytest.fixture(autouse=True)
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Give ``conf_`` default settings so ``connect_ssh`` can read the timeout."""
    settings = Settings()
    monkeypatch.setattr(conf_, "settings", settings, raising=False)
    return settings


class FakeMessageBox:
    """Records every box; ``answers`` are consumed per ``run()``; ``hooks`` run when a titled box is shown."""

    shown: ClassVar[list[FakeMessageBox]] = []
    answers: ClassVar[list[Response]] = []
    hooks: ClassVar[dict[str, Callable[[], None]]] = {}

    def __init__(
        self,
        message: str,
        title: str = "Error",
        buttons: list[object] | None = None,
        variant: str = "default",
        *,
        width: str | None = None,
    ) -> None:
        self.message, self.title, self.buttons, self.variant = message, title, buttons, variant
        FakeMessageBox.shown.append(self)

    async def run(self) -> Response:
        hook = FakeMessageBox.hooks.get(self.title)
        if hook is not None:
            hook()
        return FakeMessageBox.answers.pop(0) if FakeMessageBox.answers else Response.OK


class FakeCredentialsDialog:
    runs: ClassVar[int] = 0
    hostnames: ClassVar[list[str]] = []
    on_run: ClassVar[Callable[[], None] | None] = None

    def __init__(self, hostname: str, username: str = "") -> None:
        FakeCredentialsDialog.hostnames.append(hostname)
        self.credentials = Credentials("testuser", "testpass")

    async def run(self) -> Response:
        FakeCredentialsDialog.runs += 1
        if FakeCredentialsDialog.on_run is not None:
            FakeCredentialsDialog.on_run()
        return Response.OK


class FlowEnv:
    def __init__(self, server: StubSSHServer, locations: KnownHostsLocations, identity: Path) -> None:
        self.server = server
        self.locations = locations
        self.identity = identity
        self.constructions = 0

    @property
    def name(self) -> str:
        return host_key_name(self.server.host, self.server.port)

    @property
    def connection(self) -> RemoteConnection:
        ssh = SshSettings(host=self.server.host, port=self.server.port, user="testuser", identity_file=str(self.identity))
        return RemoteConnection(name="stub", ssh=ssh)

    @property
    def known_hosts(self) -> Path:
        return self.locations.user_known_hosts

    def write_known_hosts(self, text: str) -> None:
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        self.known_hosts.write_text(text)

    @property
    def boxes(self) -> list[FakeMessageBox]:
        return FakeMessageBox.shown


@pytest.fixture
def flow_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[FlowEnv, None, None]:
    FakeMessageBox.shown = []
    FakeMessageBox.answers = []
    FakeMessageBox.hooks = {}
    FakeCredentialsDialog.runs = 0
    FakeCredentialsDialog.hostnames = []
    FakeCredentialsDialog.on_run = None
    (tmp_path / "root").mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    locations = KnownHostsLocations.under(tmp_path / "home", tmp_path / "etc")
    monkeypatch.setattr(KnownHostsLocations, "default", classmethod(lambda _cls: locations))
    identity = tmp_path / "id_flow"
    private = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    identity.write_bytes(private)
    server = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"), reject_publickey=True)
    server.start()
    env = FlowEnv(server, locations, identity)

    class _Counting(SSHFilesystem):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            env.constructions += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ssh_module, "MessageBox", FakeMessageBox)
    monkeypatch.setattr(ssh_module, "CredentialsDialog", FakeCredentialsDialog)
    monkeypatch.setattr(ssh_module, "SSHFilesystem", _Counting)
    yield env
    server.stop()
