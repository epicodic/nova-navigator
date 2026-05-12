"""GlobalConfig — singleton holding all loaded config objects."""

import typing

from .bookmarks import BookmarkConfig
from .filetypes import FileTypeConfig
from .remotes import RemoteConfig
from .settings import Settings

__all__ = ["GlobalConfig", "conf_"]


class GlobalConfig:
    """Singleton holding all loaded config objects."""

    filetypes: FileTypeConfig
    bookmarks: BookmarkConfig
    remotes: RemoteConfig
    settings: Settings

    def load_all_configs(self) -> None:
        """Load all config files, creating them from defaults if missing."""
        for name, cls in typing.get_type_hints(GlobalConfig).items():
            setattr(self, name, cls.load())

    def write_all_configs(self) -> None:
        """Save all config files."""
        for name in GlobalConfig.__annotations__:
            getattr(self, name).save()


conf_ = GlobalConfig()
