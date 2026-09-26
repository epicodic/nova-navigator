from __future__ import annotations

from pathlib import Path, PurePath

import pytest


def test_filetypes_default_construction_has_default_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    assert cfg._default_section is not None
    assert cfg._default_section.section_name == "default"


def test_filetypes_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.filetypes import FileTypeConfig

    FileTypeConfig.load()
    assert (tmp_path / "filetypes.toml").exists()


def test_filetypes_get_icon_for_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    section = cfg._find_section_for_path(PurePath("movie.mp4"))
    assert section.icon == "video"


def test_filetypes_get_open_command_for_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    cmd = cfg.get_open_command_for_file_path(PurePath("/home/user/doc.pdf"))
    assert isinstance(cmd, list)
    assert len(cmd) > 0


def test_filetypes_get_colors_returns_tuple(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    color, bg = cfg.get_colors_for_filename("file.txt")
    assert isinstance(color, str) or color is None
    assert isinstance(bg, str) or bg is None


def test_filetypes_unknown_extension_uses_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    section = cfg._find_section_for_path(PurePath("mystery.xyzzy"))
    assert section.section_name == "default"


def test_open_rule_uses_custom_section_wait_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    (tmp_path / "filetypes.toml").write_text(
        """[default]
open = "xdg-open %f"
[edit]
regex = '\\.edit$'
open = "editor %f"
wait_for_exit = true
"""
    )

    from nova_navigator.config.filetypes import FileTypeConfig, OpenRule

    cfg = FileTypeConfig.load()
    rule = cfg.get_open_rule_for_file_path(PurePath("document.edit"))

    assert rule == OpenRule(command=["editor", "document.edit"], wait_for_exit=True)


def test_open_rule_default_xdg_open_does_not_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    rule = cfg.get_open_rule_for_file_path(PurePath("mystery.xyzzy"))

    assert rule.command == ["xdg-open", "mystery.xyzzy"]
    assert rule.wait_for_exit is False


def test_open_rule_falls_back_to_default_command_and_wait_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    (tmp_path / "filetypes.toml").write_text(
        """[default]
open = "editor %f"
wait_for_exit = true
[edit]
regex = '\\.edit$'
wait_for_exit = false
"""
    )

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    rule = cfg.get_open_rule_for_file_path(PurePath("document.edit"))

    assert rule.command == ["editor", "document.edit"]
    assert rule.wait_for_exit is True


def test_open_rule_whitespace_command_falls_back_to_default_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    (tmp_path / "filetypes.toml").write_text(
        """[default]
open = "editor %f"
wait_for_exit = true
[edit]
regex = '\\.edit$'
open = "   "
wait_for_exit = false
"""
    )

    from nova_navigator.config.filetypes import FileTypeConfig

    cfg = FileTypeConfig.load()
    rule = cfg.get_open_rule_for_file_path(PurePath("document.edit"))

    assert rule.command == ["editor", "document.edit"]
    assert rule.wait_for_exit is True
