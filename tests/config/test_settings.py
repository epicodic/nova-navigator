from __future__ import annotations

from pathlib import Path

import pytest


def test_settings_default_construction() -> None:
    from nova_navigator.config.settings import Settings

    s = Settings()
    assert s.general.show_hidden_files is False
    assert s.general.confirm_delete is True
    assert s.network.ssh_timeout == 30
    assert not hasattr(s.network, "proxy")
    assert s.terminal.scrollback_lines == 1000


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
    assert "Embedded terminal panel settings" in content


def test_settings_save_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    instance = Settings.load()
    instance.general.show_hidden_files = True
    instance.network.ssh_timeout = 60
    instance.terminal.scrollback_lines = 5000
    instance.save()

    reloaded = Settings.load()
    assert reloaded.general.show_hidden_files is True
    assert reloaded.network.ssh_timeout == 60
    assert reloaded.terminal.scrollback_lines == 5000


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


def test_settings_new_file_has_no_proxy_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    Settings.load()
    assert "proxy" not in (tmp_path / "settings.toml").read_text()


def test_settings_with_legacy_proxy_key_loads_and_save_keeps_other_settings_and_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "# user note\n[general]\n# keep me\nshow_hidden_files = true\nconfirm_delete = false\n[network]\nssh_timeout = 77\nproxy = 'http://proxy:3128'\n[terminal]\nscrollback_lines = 4000\n"
    )

    s = Settings.load()
    assert s.network.ssh_timeout == 77
    assert not hasattr(s.network, "proxy")
    s.general.use_binary_sizes = True
    s.save()

    content = settings_file.read_text()
    for expected in (
        "# user note",
        "# keep me",
        "ssh_timeout = 77",
        "show_hidden_files = true",
        "confirm_delete = false",
        "scrollback_lines = 4000",
        "use_binary_sizes = true",
    ):
        assert expected in content

    reloaded = Settings.load()
    assert reloaded.network.ssh_timeout == 77
    assert reloaded.general.show_hidden_files is True
    assert reloaded.general.confirm_delete is False
    assert reloaded.general.use_binary_sizes is True
    assert reloaded.terminal.scrollback_lines == 4000


def test_general_settings_default_key_display_style() -> None:
    from nova_navigator.config.settings import GeneralSettings
    from nova_widgets.keymap.key_sequence import KeyFormatStyle

    s = GeneralSettings()
    assert s.key_display_style == KeyFormatStyle.CLASSIC


def test_settings_key_display_style_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_widgets.keymap.key_sequence import KeyFormatStyle

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    s = Settings.load()
    assert s.general.key_display_style == KeyFormatStyle.CLASSIC


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


def test_copy_move_settings_default_off() -> None:
    from nova_navigator.config.settings import Settings

    s = Settings()
    assert s.copy_move.follow_links is False
    assert s.copy_move.stable_symlinks is False


def test_copy_move_settings_section_comment_in_new_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_navigator.config.settings import Settings

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    Settings.load()
    content = (tmp_path / "settings.toml").read_text()
    assert "[copy_move]" in content
    assert "Copy and move settings" in content
    assert "follow_links = false" in content
    assert "stable_symlinks = false" in content


def test_copy_move_missing_section_loads_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_navigator.config.settings import Settings

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    (tmp_path / "settings.toml").write_text("[general]\nshow_hidden_files = true\n")

    instance = Settings.load()
    assert instance.copy_move.follow_links is False
    assert instance.copy_move.stable_symlinks is False


def test_copy_move_missing_key_loads_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_navigator.config.settings import Settings

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    (tmp_path / "settings.toml").write_text("[copy_move]\nfollow_links = true\n")

    instance = Settings.load()
    assert instance.copy_move.follow_links is True
    assert instance.copy_move.stable_symlinks is False


def test_copy_move_save_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_navigator.config.settings import Settings

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    instance = Settings.load()
    instance.copy_move.follow_links = True
    instance.copy_move.stable_symlinks = True
    instance.save()

    reloaded = Settings.load()
    assert reloaded.copy_move.follow_links is True
    assert reloaded.copy_move.stable_symlinks is True


def test_copy_move_save_keeps_comments_and_other_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    from nova_navigator.config.settings import Settings

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "# user note\n[general]\n# keep me\nshow_hidden_files = true\n[network]\nssh_timeout = 77\n",
    )

    instance = Settings.load()
    instance.copy_move.follow_links = True
    instance.save()

    content = settings_file.read_text()
    assert "# user note" in content
    assert "# keep me" in content
    assert "ssh_timeout = 77" in content
    assert "show_hidden_files = true" in content
    reloaded = Settings.load()
    assert reloaded.copy_move.follow_links is True
    assert reloaded.copy_move.stable_symlinks is False
    assert reloaded.network.ssh_timeout == 77
