from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN = ("load_system_host_keys", "load_host_keys", "save_host_keys", "AutoAddPolicy")
NO_OPENSSH = ("nova_navigator/remotes/known_hosts.py", "nova_navigator/vfs/filesystems/ssh.py")


def test_no_forbidden_host_key_api_in_src() -> None:
    hits = [f"{p.relative_to(SRC)}: {word}" for p in SRC.rglob("*.py") for word in FORBIDDEN if word in p.read_text(encoding="utf-8")]
    assert hits == []


@pytest.mark.parametrize("relative", NO_OPENSSH)
def test_connect_path_does_not_call_openssh_binaries(relative: str) -> None:
    text = (SRC / relative).read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert "ssh-keygen" not in text
    assert "os.system" not in text


def test_known_hosts_module_is_textual_and_vfs_free() -> None:
    text = (SRC / "nova_navigator/remotes/known_hosts.py").read_text(encoding="utf-8")
    assert "textual" not in text
    assert "nova_navigator.vfs" not in text
