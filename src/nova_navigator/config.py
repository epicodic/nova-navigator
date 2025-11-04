# from __future__ import annotations

import mimetypes
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Self

import tomlkit

from .icon_set import ICONS
from .toml_config import Field, TomlConfig, TOMLTable

__all__ = (
    "GLOBAL_CONFIG",
    "get_config_file_path",
)


def _get_config_path(appname: str) -> Path:
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / appname
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / appname
    # Linux, BSD, etc.
    return Path.home() / ".config" / appname


_APP_CONFIG_DIR: Path = _get_config_path("nova_navigator")
_DEFAULT_CONFIG_DIR: Path = Path(__file__).parent.parent.parent / "config" / "default"


def _compile_pattern(pattern_str: str | None) -> re.Pattern[str] | None:
    return re.compile(pattern_str) if pattern_str else None


@dataclass
class TomlFile:
    file_path: Path
    toml: tomlkit.TOMLDocument


class ConfigBase:
    CONFIG_NAME: str
    _toml_file: TomlFile

    def __init__(self, toml_file: TomlFile) -> None:
        self._toml_file = toml_file

    @classmethod
    def load(cls) -> Self:
        if not hasattr(cls, "CONFIG_NAME"):
            raise AttributeError(f"Class {cls.__name__} is missing 'CONFIG_NAME' class variable")
        name = cls.CONFIG_NAME  # pyright: ignore[reportAttributeAccessIssue]
        config_file_path = _APP_CONFIG_DIR / f"{name}.toml"
        try:
            with open(config_file_path) as f:
                toml = tomlkit.load(f)
        except FileNotFoundError:
            default_file_path = _DEFAULT_CONFIG_DIR / f"{name}.toml"
            with open(default_file_path) as f:
                toml = tomlkit.load(f)

        toml_file = TomlFile(file_path=config_file_path, toml=toml)
        return cls(toml_file)


class Config(ConfigBase, TomlConfig):
    def __init__(self, toml_file: TomlFile) -> None:
        ConfigBase.__init__(self, toml_file)
        TomlConfig.__init__(self, toml_file.toml)


class FileTypeConfig(ConfigBase):
    CONFIG_NAME = "filetypes"

    class Section(TomlConfig):
        name: str = Field(exclude=True)
        mimetype: str | None
        mimetype_pattern: re.Pattern[str] = Field(
            default_factory=lambda section: _compile_pattern(section.mimetype), exclude=True
        )
        regex: str | None
        regex_pattern: re.Pattern[str] = Field(
            default_factory=lambda section: _compile_pattern(section.regex), exclude=True
        )
        open: str | None
        open_cmd: list[str] | None = Field(
            default_factory=lambda section: section.open.split() if section.open else None, exclude=True
        )
        color: str | None
        icon: str | None
        background_color: str | None

    _sections: list[Section]
    _default_section: Section

    def __init__(self, toml_file: TomlFile) -> None:
        super().__init__(toml_file)
        self._sections = [self.Section(item, name=name) for name, item in toml_file.toml.items()]

        default_section = next((ft for ft in self._sections if ft.name == "default"), None)

        if not default_section:
            raise ValueError(f"Extension config missing 'default' section in {toml_file.file_path}'")

        assert default_section
        self._default_section = default_section

    def _replace_variables(self, cmd: list[str], path: PurePath) -> list[str]:
        # %f : current file
        cmd = [c.replace("%f", str(path)) for c in cmd]
        # %d : current directory
        cmd = [c.replace("%d", str(path.parent)) for c in cmd]
        return cmd  # noqa: RET504

    def _find_section_for_path(self, path: PurePath) -> "Section":
        mimetype = mimetypes.guess_type(path.as_posix())[0]
        for file_type in self._sections:
            if file_type.mimetype and mimetype and file_type.mimetype_pattern.search(mimetype):
                return file_type
            if file_type.regex and file_type.regex_pattern.search(path.as_posix()):
                return file_type
        return self._default_section

    def get_open_command_for_file_path(self, path: PurePath) -> list[str]:
        section = self._find_section_for_path(path)
        open_cmd = section.open_cmd
        if not section.open_cmd:
            open_cmd = self._default_section.open_cmd

        assert open_cmd is not None
        return self._replace_variables(open_cmd, path)

    def get_colors_for_filename(self, filename: str) -> tuple[str | None, str | None]:
        section = self._find_section_for_path(PurePath(filename))
        return section.color, section.background_color

    def get_icon_for_filename(self, filename: str, default: str) -> str:
        section = self._find_section_for_path(PurePath(filename))
        if not section.icon:
            return default

        return ICONS.get_icon(section.icon, default=default)


class BookmarkConfig(ConfigBase):
    CONFIG_NAME = "bookmarks"

    class Bookmark(TomlConfig):
        name: str = Field(exclude=True)
        path: str
        icon: str | None

    class Group(TomlConfig):
        name: str = Field(exclude=True)
        bookmarks: list["BookmarkConfig.Bookmark"] = Field(exclude=True)
        icon: str | None

    # bookmarks: list[Bookmark]
    groups: list[Group]

    def __init__(self, toml_file: TomlFile) -> None:
        super().__init__(toml_file)

        self.groups = []
        for name, item in toml_file.toml.items():
            print(name)
            print(item)

            group = self.Group(item, name=name)
            group.bookmarks = [
                self.Bookmark(bm, name=bm_name) for bm_name, bm in item.items() if isinstance(bm, TOMLTable)
            ]
            self.groups.append(group)


# singleton
class GlobalConfig:
    filetypes: FileTypeConfig
    bookmarks: BookmarkConfig

    def load_all_configs(self) -> None:
        for name, cls in GlobalConfig.__annotations__.items():
            config_instance = cls.load()
            setattr(self, name, config_instance)

    def write_all_configs(self) -> None:
        raise NotImplementedError


def get_config_file_path(config_filename: str) -> Path:
    config_file_path = _APP_CONFIG_DIR / f"{config_filename}"
    # if not config_file_path.exists():
    #    shutil.copy(
    #        _DEFAULT_CONFIG_DIR / config_filename,
    #        config_file_path,
    #    )
    return _DEFAULT_CONFIG_DIR / config_filename  # config_file_path


GLOBAL_CONFIG = GlobalConfig()
