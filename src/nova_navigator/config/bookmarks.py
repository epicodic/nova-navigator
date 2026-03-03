"""BookmarkConfig — user bookmark groups and entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from nova_navigator.config.loader import ModelConfig
from nova_navigator.config.model import BaseModel


@dataclass
class Bookmark(BaseModel):
    name: str = ""
    path: str = ""
    icon: str | None = None


@dataclass
class Group(BaseModel):
    name: str = ""
    icon: str | None = None
    bookmarks: list[Bookmark] = field(default_factory=list)


def _default_groups() -> list[Group]:
    return [
        Group(
            name="Computer",
            icon="computer",
            bookmarks=[
                Bookmark(name="Home", path="$HOME", icon="house"),
                Bookmark(name="Documents", path="$HOME/Documents", icon="file"),
                Bookmark(name="Downloads", path="$HOME/Downloads", icon="download"),
                Bookmark(name="Filesystem", path="/", icon="open_folder"),
            ],
        ),
        Group(
            name="Bookmarks",
            icon="bookmark",
            bookmarks=[],
        ),
    ]


@dataclass
class BookmarkConfig(BaseModel, ModelConfig):
    """Bookmark configuration."""

    CONFIG_NAME: ClassVar[str] = "bookmarks"

    groups: list[Group] = field(default_factory=_default_groups)
