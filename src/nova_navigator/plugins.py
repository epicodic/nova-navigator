"""FilesystemPlugin and PluginRegistry — single wiring site for filesystem plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.scheme_registry import Connector, SchemeRegistry

if TYPE_CHECKING:
    from nova_navigator.terminal.terminal_pool import TerminalFactory, TerminalPool


@dataclass
class FilesystemPlugin:
    """Self-contained descriptor for a filesystem type.

    Carries everything needed to register the filesystem with the application:
    the URI scheme, the connector for resolving URIs, the concrete filesystem
    type (used for isinstance dispatch in TerminalPool), and an optional
    terminal factory callable.
    """

    scheme: str
    fs_type: type[Filesystem]
    connector: Connector
    terminal_factory: TerminalFactory | None = field(default=None)


class PluginRegistry:
    """Composition root that wires FilesystemPlugins into SchemeRegistry and TerminalPool."""

    def __init__(
        self,
        scheme_registry: SchemeRegistry,
        terminal_pool: TerminalPool,
    ) -> None:
        self._scheme_registry = scheme_registry
        self._terminal_pool = terminal_pool

    def register(self, plugin: FilesystemPlugin) -> None:
        """Register *plugin* into the scheme registry and terminal pool."""
        self._scheme_registry.register_scheme(plugin.scheme, plugin.connector)
        if plugin.terminal_factory is not None:
            fs_type = plugin.fs_type
            factory = plugin.terminal_factory
            self._terminal_pool.register_factory(
                lambda fs, t=fs_type: isinstance(fs, t),
                factory,
            )
