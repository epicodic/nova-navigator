"""Behaviour of Azure access when the `azure` extra is not installed."""

import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest
from textual.content import Content

from nova_navigator.config.remotes import AzureSettings, RemoteConnection
from nova_navigator.remotes.azure import AzureConnector, connect_azure
from nova_navigator.vfs.filesystems import AzureFilesystem, MissingAzureExtraError

_ACCOUNT_URL = "https://account.blob.core.windows.net"


@pytest.fixture
def no_azure_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "nova_navigator.vfs.filesystems._azure_sdk", raising=False)
    for name in ("azure.core.exceptions", "azure.identity", "azure.storage.blob"):
        monkeypatch.setitem(sys.modules, name, None)


def test_startup_imports_no_azure_sdk() -> None:
    code = "import sys\nimport nova_navigator.nova_navigator\nbad = [m for m in sys.modules if m.startswith(('azure.core', 'azure.identity', 'azure.storage'))]\nassert not bad, bad\n"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.usefixtures("no_azure_sdk")
def test_constructor_raises_defined_error() -> None:
    with pytest.raises(MissingAzureExtraError, match=r"nova-navigator\[azure\]"):
        AzureFilesystem(_ACCOUNT_URL, "container")


def _assert_missing_extra_box(box: AsyncMock) -> None:
    message = box.call_args.args[0]
    assert "nova-navigator[azure]" in Content.from_markup(message).plain


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_azure_sdk")
async def test_saved_connection_shows_message(caplog: pytest.LogCaptureFixture) -> None:
    conn = RemoteConnection(name="blob", azure=AzureSettings(account_url=_ACCOUNT_URL, container="c"))
    with patch("nova_navigator.remotes.azure.MessageBox") as box:
        box.return_value.run = AsyncMock()
        assert await connect_azure(conn) is None
    _assert_missing_extra_box(box)
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_azure_sdk")
async def test_azure_uri_shows_message() -> None:
    with patch("nova_navigator.remotes.azure.MessageBox") as box:
        box.return_value.run = AsyncMock()
        assert await AzureConnector().resolve("/container/file", "account.blob.core.windows.net") is None
    _assert_missing_extra_box(box)
