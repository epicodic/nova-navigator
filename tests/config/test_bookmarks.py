from __future__ import annotations

from pathlib import Path

import pytest


def test_bookmarks_default_construction_has_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    cfg = BookmarkConfig.load()
    assert len(cfg.groups) > 0


def test_bookmarks_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    BookmarkConfig.load()
    assert (tmp_path / "bookmarks.toml").exists()


def test_bookmarks_groups_have_bookmarks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    cfg = BookmarkConfig.load()
    assert any(len(g.bookmarks) > 0 for g in cfg.groups)


def test_bookmarks_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    first = BookmarkConfig.load()
    group_count = len(first.groups)

    second = BookmarkConfig.load()
    assert len(second.groups) == group_count
