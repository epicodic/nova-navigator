from __future__ import annotations

from pathlib import Path

import pytest


def test_settings_default_construction() -> None:
    from nova_navigator.config.settings import Settings

    s = Settings()
    assert s.general.show_hidden_files is False
    assert s.general.confirm_delete is True
    assert s.network.ssh_timeout == 30
    assert s.network.proxy == ""


def test_settings_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    Settings.load()
    assert (tmp_path / "settings.toml").exists()


def test_settings_file_contains_section_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    Settings.load()
    content = (tmp_path / "settings.toml").read_text()
    assert "General application settings" in content
    assert "Network settings" in content


def test_settings_save_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    instance = Settings.load()
    instance.general.show_hidden_files = True
    instance.network.ssh_timeout = 60
    instance.save()

    reloaded = Settings.load()
    assert reloaded.general.show_hidden_files is True
    assert reloaded.network.ssh_timeout == 60


def test_settings_save_preserves_user_comment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("# user note\n[general]\nshow_hidden_files = false\nconfirm_delete = true\n[network]\nssh_timeout = 30\nproxy = ''\n")

    instance = Settings.load()
    instance.general.show_hidden_files = True
    instance.save()

    content = settings_file.read_text()
    assert "# user note" in content
    assert "true" in content


def test_general_settings_default_key_display_style() -> None:
    from nova_navigator.config.settings import GeneralSettings
    from nova_widgets.keymap.format import KeyDisplayStyle

    s = GeneralSettings()
    assert s.key_display_style == KeyDisplayStyle.CLASSIC


def test_settings_key_display_style_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_widgets.keymap.format import KeyDisplayStyle

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    s = Settings.load()
    assert s.general.key_display_style == KeyDisplayStyle.CLASSIC


def test_settings_ui_nerd_font_defaults_to_auto() -> None:
    from nova_navigator.config.settings import NerdFontMode, Settings

    s = Settings()
    assert s.general.use_nerd_font is NerdFontMode.AUTO


def test_settings_ui_nerd_font_round_trips_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_navigator.config.settings import NerdFontMode, Settings

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    instance = Settings.load()
    instance.general.use_nerd_font = NerdFontMode.YES
    instance.save()

    reloaded = Settings.load()
    assert reloaded.general.use_nerd_font is NerdFontMode.YES
