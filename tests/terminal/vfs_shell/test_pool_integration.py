"""Integration tests for VirtualPtyBackend availability via terminal package."""

from __future__ import annotations


def test_virtual_pty_backend_importable_from_terminal() -> None:
    """VirtualPtyBackend must be importable from nova_navigator.terminal."""
    from nova_navigator.terminal import VirtualPtyBackend

    assert VirtualPtyBackend is not None


def test_virtual_pty_backend_importable_from_vfs_shell() -> None:
    """VirtualPtyBackend must also be importable from nova_navigator.terminal.vfs_shell."""
    from nova_navigator.terminal.vfs_shell import VirtualPtyBackend

    assert VirtualPtyBackend is not None


def test_virtual_pty_backend_is_pty_backend_subclass() -> None:
    """VirtualPtyBackend must implement the PtyBackend ABC."""
    from nova_navigator.terminal import VirtualPtyBackend
    from nova_navigator.terminal.pty_backend import PtyBackend

    assert issubclass(VirtualPtyBackend, PtyBackend)
