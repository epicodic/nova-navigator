from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pytest

from nova_navigator.config.model import BaseModel, key_field

# ── Nested-model helper ───────────────────────────────────────────────────────


@dataclass
class Tag(BaseModel):
    """A tag."""

    label: str = ""


@dataclass
class NestedSettings(BaseModel):
    """Settings with a nested list of models."""

    title: str = "default"
    tags: list[Tag] = dataclasses.field(default_factory=list)


# ── Helpers — import loader under test after patching config dir ───────────────
# We patch _APP_CONFIG_DIR via monkeypatch on the loader module.


@dataclass
class SimpleSettings(BaseModel):
    """Simple test settings."""

    name: str = "default"
    count: int = 0


@dataclass
class SectionItem(BaseModel):
    section_name: str = key_field()
    value: str = "x"


# ── ModelConfig ───────────────────────────────────────────────────────────────


def test_model_config_creates_file_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple"

    instance = TConfig.load()
    config_file = tmp_path / "test_simple.toml"
    assert config_file.exists()
    assert instance.name == "default"
    assert instance.count == 0


def test_model_config_reads_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple2"

    config_file = tmp_path / "test_simple2.toml"
    config_file.write_text('name = "loaded"\ncount = 7\n')

    instance = TConfig.load()
    assert instance.name == "loaded"
    assert instance.count == 7


def test_model_config_save_updates_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple3"

    instance = TConfig.load()
    instance.name = "updated"
    instance.save()

    content = (tmp_path / "test_simple3.toml").read_text()
    assert "updated" in content


def test_model_config_save_updates_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save() rewrites the file from scratch; updated values must be present."""
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple4"

    config_file = tmp_path / "test_simple4.toml"
    config_file.write_text("# user comment\nname = 'old'\ncount = 0\n")

    instance = TConfig.load()
    instance.name = "new"
    instance.save()

    content = (tmp_path / "test_simple4.toml").read_text()
    assert "new" in content


def test_model_config_save_updates_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save() rewrites the file; the updated field value must be present."""
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_inline_comment"

    config_file = tmp_path / "test_inline_comment.toml"
    config_file.write_text('name = "old"  # inline comment\ncount = 3\n')

    instance = TConfig.load()
    instance.count = 99
    instance.save()

    content = (tmp_path / "test_inline_comment.toml").read_text()
    assert "99" in content


def test_model_config_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_round_trip"

    config_file = tmp_path / "test_round_trip.toml"
    config_file.write_text("# preserved\nname = 'original'\ncount = 1\n")

    instance = TConfig.load()
    assert instance.name == "original"
    instance.name = "changed"
    instance.count = 42
    instance.save()

    # reload from disk and verify persisted values
    instance2 = TConfig.load()
    assert instance2.name == "changed"
    assert instance2.count == 42


def test_model_config_save_without_prior_load_creates_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save() should work even when _toml_doc is absent (no prior load call)."""
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_save_no_load"

    instance = TConfig()
    instance.name = "direct"
    instance.save()

    content = (tmp_path / "test_save_no_load.toml").read_text()
    assert "direct" in content


def test_model_config_creates_parent_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    nested_dir = tmp_path / "a" / "b" / "c"
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", nested_dir)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_nested"

    TConfig.load()
    assert (nested_dir / "test_nested.toml").exists()


def test_model_config_save_preserves_user_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save() must preserve comments the user wrote in the config file.

    This test documents the desired behaviour.  It currently FAILS because
    save() rewrites the document from scratch (to_toml), discarding comments.
    """
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_comments_preserved"

    config_file = tmp_path / "test_comments_preserved.toml"
    config_file.write_text("# user comment\nname = 'old'\ncount = 0\n")

    instance = TConfig.load()
    instance.name = "new"
    instance.save()

    content = (tmp_path / "test_comments_preserved.toml").read_text()
    assert "# user comment" in content, "user comments must survive save()"
    assert "new" in content


def test_model_config_save_serialises_list_of_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save() must persist list[ConfigModel] fields.

    This test documents the bug present when save() used update_toml_doc,
    which silently skipped list[ConfigModel] fields (e.g. bookmark groups).
    """
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(NestedSettings, ModelConfig):
        CONFIG_NAME = "test_nested_list"

    instance = TConfig.load()
    instance.tags = [Tag(label="alpha"), Tag(label="beta")]
    instance.save()

    instance2 = TConfig.load()
    assert len(instance2.tags) == 2
    assert instance2.tags[0].label == "alpha"
    assert instance2.tags[1].label == "beta"


# ── ListConfig ────────────────────────────────────────────────────────────────


def test_list_config_creates_file_from_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ListConfig

    class TListConfig(ListConfig):
        CONFIG_NAME = "test_list"

        @classmethod
        def default_items(cls) -> list[BaseModel]:
            return [SectionItem(section_name="first", value="aaa")]

    instance = TListConfig.load()
    assert (tmp_path / "test_list.toml").exists()
    assert len(instance._items) == 1
    assert instance._items[0].section_name == "first"


def test_list_config_reads_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ListConfig

    class TListConfig(ListConfig):
        CONFIG_NAME = "test_list2"

        @classmethod
        def default_items(cls) -> list[BaseModel]:
            return []

    (tmp_path / "test_list2.toml").write_text('[mykey]\nvalue = "loaded"\n')
    instance = TListConfig.load()
    assert len(instance._items) == 1
    assert instance._items[0].section_name == "mykey"
    assert instance._items[0].value == "loaded"


def test_list_config_save_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ListConfig

    class TListConfig(ListConfig):
        CONFIG_NAME = "test_list_save"
        _item_cls = SectionItem

        @classmethod
        def default_items(cls) -> list[BaseModel]:
            return [SectionItem(section_name="first", value="aaa")]

    instance = TListConfig.load()
    instance._items[0].value = "bbb"  # type: ignore[attr-defined]
    instance.save()

    # reload from disk
    instance2 = TListConfig.load()
    assert instance2._items[0].value == "bbb"


def test_list_config_save_raises_for_untyped_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ListConfig

    class TListConfig(ListConfig):
        CONFIG_NAME = "test_list_untyped"

        @classmethod
        def default_items(cls) -> list[BaseModel]:
            return []

    (tmp_path / "test_list_untyped.toml").write_text('[mykey]\nvalue = "loaded"\n')
    instance = TListConfig.load()
    with pytest.raises(RuntimeError, match="_item_cls"):
        instance.save()
