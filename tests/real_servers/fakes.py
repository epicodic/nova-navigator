"""Stand-ins for the interactive dialogs of the connect flow, and the factory type the ``connect`` fixture returns."""

from __future__ import annotations

from typing import ClassVar, Protocol

from nova_navigator.dialogs.credentials_dialog import Credentials
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_widgets import Response
from tests.real_servers.ssh import SshServer


class FakeMessageBox:
    """Records every box shown; ``answers`` are consumed one per ``run()``, the default answer is OK."""

    shown: ClassVar[list[FakeMessageBox]] = []
    answers: ClassVar[list[Response]] = []

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
        return FakeMessageBox.answers.pop(0) if FakeMessageBox.answers else Response.OK


class FakeCredentialsDialog:
    """Hands out ``credentials_to_give``; when it is None the user cancels."""

    credentials_to_give: ClassVar[Credentials | None] = None
    runs: ClassVar[int] = 0

    def __init__(self, hostname: str, username: str = "") -> None:
        self.credentials = FakeCredentialsDialog.credentials_to_give

    async def run(self) -> Response:
        FakeCredentialsDialog.runs += 1
        return Response.OK if FakeCredentialsDialog.credentials_to_give is not None else Response.CANCEL


class ConnectFactory(Protocol):
    """Opens an `SSHFilesystem` to *server*, with key or password authentication, optionally to another host or port."""

    async def __call__(
        self,
        server: SshServer,
        *,
        key: bool = True,
        host: str | None = None,
        port: int | None = None,
    ) -> SSHFilesystem: ...
