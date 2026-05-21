"""Azure Blob Storage connection establishment."""

from __future__ import annotations

import asyncio
import logging

from nova_navigator.config.remotes import RemoteConnection
from nova_navigator.dialogs import MessageBox
from nova_navigator.plugins import FilesystemPlugin
from nova_navigator.vfs.filesystems import AzureFilesystem
from nova_navigator.vfs.vpath import VPath

_logger = logging.getLogger(__name__)


async def connect_azure(conn: RemoteConnection) -> AzureFilesystem | None:
    """Establish an Azure Blob Storage connection.

    Returns the connected `AzureFilesystem`, or `None` if the connection failed.
    `conn.azure` must not be `None`.
    """
    assert conn.azure is not None
    azure = conn.azure
    proxy_url: str | None = None
    if conn.proxy and conn.proxy.host:
        proxy_url = f"http://{conn.proxy.host}:{conn.proxy.port}"
    _logger.info(
        "Azure connect: name=%r account_url=%r container=%r proxy=%r managed_identity=%r",
        conn.name,
        azure.account_url,
        azure.container,
        proxy_url,
        azure.use_managed_identity,
    )
    try:
        fs = await asyncio.to_thread(
            AzureFilesystem,
            azure.account_url,
            azure.container,
            proxy_url=proxy_url,
            managed_identity=azure.use_managed_identity,
        )
    except Exception as exc:
        _logger.exception(
            "Azure connection failed for %r (account_url=%r container=%r): %s",
            conn.name,
            azure.account_url,
            azure.container,
            exc,
        )
        await MessageBox(
            f"Could not connect to {conn.name!r}:\n"
            f"account_url: {azure.account_url}\n"
            f"container:   {azure.container}\n\n"
            f"{exc}",
            variant="error",
        ).run()
        return None
    _logger.info("Azure connection established for %r", conn.name)
    return fs


class AzureConnector:
    async def resolve(self, path: str, netloc: str | None) -> VPath | None:
        account_url = f"https://{netloc}"
        parts = path.lstrip("/").split("/", 1)
        container = parts[0]
        blob_path = "/" + parts[1] if len(parts) > 1 else "/"
        if blob_path in ("//", "/"):
            blob_path = "/"
        fs = AzureFilesystem(account_url, container)
        return fs.path(blob_path)


AZURE_PLUGIN = FilesystemPlugin(
    scheme="azure",
    fs_type=AzureFilesystem,
    connector=AzureConnector(),
    terminal_factory=None,  # virtual fallback terminal not yet implemented
)
