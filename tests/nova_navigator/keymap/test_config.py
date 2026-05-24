from __future__ import annotations

from pathlib import Path

from nova_navigator.keymap.config import KeybindingsConfig
from nova_widgets.menu._action import Action


def test_config_default_key_used_when_no_file(tmp_path: Path) -> None:
    actions = [
        Action("Copy", name="browser.copy", key="f5"),
    ]
    cfg = KeybindingsConfig(config_dir=tmp_path)
    bindings = cfg.resolve(actions)
    assert bindings["browser.copy"] == "f5"


def test_config_file_overrides_default(tmp_path: Path) -> None:
    (tmp_path / "keybindings.toml").write_text('[bindings]\n"browser.copy" = "f6"\n')
    actions = [
        Action("Copy", name="browser.copy", key="f5"),
    ]
    cfg = KeybindingsConfig(config_dir=tmp_path)
    bindings = cfg.resolve(actions)
    assert bindings["browser.copy"] == "f6"


def test_config_empty_string_unmaps_default(tmp_path: Path) -> None:
    (tmp_path / "keybindings.toml").write_text('[bindings]\n"browser.copy" = ""\n')
    actions = [
        Action("Copy", name="browser.copy", key="f5"),
    ]
    cfg = KeybindingsConfig(config_dir=tmp_path)
    bindings = cfg.resolve(actions)
    assert bindings.get("browser.copy") in (None, "")


def test_config_save_creates_file(tmp_path: Path) -> None:
    cfg = KeybindingsConfig(config_dir=tmp_path)
    cfg.save({"browser.copy": "f5", "app.quit": "ctrl+q"})
    assert (tmp_path / "keybindings.toml").exists()


def test_config_save_load_roundtrip(tmp_path: Path) -> None:
    cfg = KeybindingsConfig(config_dir=tmp_path)
    cfg.save({"browser.copy": "f6", "app.quit": "ctrl+q"})
    actions = [
        Action("Copy", name="browser.copy", key="f5"),
        Action("Quit", name="app.quit", key="ctrl+q"),
    ]
    bindings = cfg.resolve(actions)
    assert bindings["browser.copy"] == "f6"
    assert bindings["app.quit"] == "ctrl+q"
