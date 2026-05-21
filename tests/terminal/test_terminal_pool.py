from __future__ import annotations

from unittest.mock import MagicMock

from nova_navigator.terminal.terminal_pool import TerminalPool
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem


def _make_terminal(display: bool = True) -> MagicMock:
    t = MagicMock()
    t.display = display
    t.styles = MagicMock()
    t.styles.width = 80
    t.styles.height = 24
    return t


def test_active_terminal_is_local_initially() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)
    assert pool.active_terminal is local


def test_switch_to_local_is_noop() -> None:
    local = _make_terminal(display=True)
    pool = TerminalPool(local)
    pool.switch_to(LocalFilesystem.singleton())
    assert local.display is True  # unchanged


def test_switch_to_registered_terminal_changes_active() -> None:
    local = _make_terminal(display=True)
    remote_terminal = _make_terminal(display=False)
    pool = TerminalPool(local)

    inner_fs = MagicMock()
    inner_fs.unwrap.return_value = inner_fs
    pool.register(inner_fs, remote_terminal)

    pool.switch_to(inner_fs)

    assert pool.active_terminal is remote_terminal
    assert local.display is False
    assert remote_terminal.display is True


def test_switch_to_copies_styles_from_active() -> None:
    local = _make_terminal(display=True)
    local.styles.width = 120
    local.styles.height = 30

    remote_terminal = _make_terminal(display=False)
    pool = TerminalPool(local)

    inner_fs = MagicMock()
    inner_fs.unwrap.return_value = inner_fs
    pool.register(inner_fs, remote_terminal)

    pool.switch_to(inner_fs)

    assert remote_terminal.styles.width == 120
    assert remote_terminal.styles.height == 30


def test_switch_to_unregistered_fs_falls_back_to_local() -> None:
    local = _make_terminal(display=True)
    pool = TerminalPool(local)

    unknown_fs = MagicMock()
    unknown_fs.unwrap.return_value = unknown_fs
    pool.switch_to(unknown_fs)  # should not raise

    assert pool.active_terminal is local


def test_switch_to_unwraps_remote_filesystem() -> None:
    local = _make_terminal(display=True)
    remote_terminal = _make_terminal(display=False)
    pool = TerminalPool(local)

    inner_fs = MagicMock()
    inner_fs.unwrap.return_value = inner_fs
    pool.register(inner_fs, remote_terminal)

    wrapper_fs = RemoteFilesystem("wrap", inner_fs)
    pool.switch_to(wrapper_fs)

    assert pool.active_terminal is remote_terminal


def test_create_for_uses_registered_factory() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)

    inner_fs = MagicMock()
    inner_fs.unwrap.return_value = inner_fs

    created = _make_terminal()
    factory = MagicMock(return_value=created)
    pool.register_factory(lambda fs: fs is inner_fs, factory)

    result = pool.create_for(inner_fs)
    assert result is created
    factory.assert_called_once_with(inner_fs)


def test_create_for_returns_none_when_no_factory_matches() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)

    unknown_fs = MagicMock()
    unknown_fs.unwrap.return_value = unknown_fs

    result = pool.create_for(unknown_fs)
    assert result is None


def test_all_terminals_includes_local_and_registered() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)

    remote_terminal = _make_terminal(display=False)
    inner_fs = MagicMock()
    inner_fs.unwrap.return_value = inner_fs
    pool.register(inner_fs, remote_terminal)

    all_t = list(pool.all_terminals())
    assert local in all_t
    assert remote_terminal in all_t


def test_has_terminal_true_for_local() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)
    assert pool.has_terminal(LocalFilesystem.singleton()) is True


def test_has_terminal_false_before_register() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)

    unknown_fs = MagicMock()
    unknown_fs.unwrap.return_value = unknown_fs
    assert pool.has_terminal(unknown_fs) is False


def test_has_terminal_true_after_register() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)

    remote_terminal = _make_terminal(display=False)
    inner_fs = MagicMock()
    inner_fs.unwrap.return_value = inner_fs
    pool.register(inner_fs, remote_terminal)
    assert pool.has_terminal(inner_fs) is True


def test_has_terminal_unwraps_remote_filesystem() -> None:
    local = _make_terminal()
    pool = TerminalPool(local)

    inner_fs = MagicMock()
    inner_fs.unwrap.return_value = inner_fs
    pool.register(inner_fs, _make_terminal(display=False))

    wrapper_fs = RemoteFilesystem("wrap", inner_fs)
    assert pool.has_terminal(wrapper_fs) is True
