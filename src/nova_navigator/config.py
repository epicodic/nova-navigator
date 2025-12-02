import mimetypes
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import override

import tomlkit

from .icon_set import ICONS

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


class Config:
    _name: str
    _default: str
    _config: tomlkit.TOMLDocument
    _config_file_path: Path

    def __init__(self, name: str, default: str = "") -> None:
        self._name = name
        self._default = default
        self._config_file_path = _APP_CONFIG_DIR / f"{self._name}.toml"

    def load(self) -> None:
        try:
            with open(self._config_file_path) as f:
                self.config = tomlkit.load(f)
        except FileNotFoundError:
            self.config = tomlkit.loads(self._default)
            self.write()
        self._on_loaded(self.config)

    def _on_loaded(self, config: tomlkit.TOMLDocument) -> None:
        pass

    def write(self) -> None:
        try:
            _APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._config_file_path, "w") as f:
                tomlkit.dump(self.config, f)
        except Exception as e:  # noqa: BLE001
            print(f"Error writing config file {self._config_file_path}: {e}")


class ExtensionConfig(Config):
    _DEFAULT = """# Nova Navigator Extension File
[default]
open="xdg-open %f"
"""

    @dataclass
    class Section:
        name: str
        mimetype: re.Pattern[str] | None
        regex: re.Pattern[str] | None
        open_cmd: list[str] | None
        color: str | None = None
        icon: str | None = None
        background_color: str | None = None

    _sections: list[Section]
    _default_section: Section

    def __init__(self) -> None:
        super().__init__("extensions", self._DEFAULT)
        self._sections = []

    @override
    def _on_loaded(self, config: tomlkit.TOMLDocument) -> None:
        self._sections = []
        default_section = None
        for name, value in config.items():
            open_cmd = value.get("open")
            section = ExtensionConfig.Section(
                name=name,
                mimetype=_compile_pattern(value.get("mimetype")),
                regex=_compile_pattern(value.get("regex")),
                open_cmd=open_cmd.split() if open_cmd else None,
                color=value.get("color"),
                icon=value.get("icon"),
                background_color=value.get("background_color"),
            )
            self._sections.append(section)
            if name == "default":
                default_section = section

        if not default_section:
            raise ValueError(f"Extension config missing 'default' section in {self._config_file_path}'")
        self._default_section = default_section

    def _replace_variables(self, cmd: list[str], path: PurePath) -> list[str]:
        # %f : current file
        cmd = [c.replace("%f", str(path)) for c in cmd]
        # %d : current directory
        cmd = [c.replace("%d", str(path.parent)) for c in cmd]
        return cmd  # noqa: RET504

    def _find_section_for_path(self, path: PurePath) -> "ExtensionConfig.Section":
        mimetype = mimetypes.guess_type(path.as_posix())[0]
        for section in self._sections:
            if section.mimetype and mimetype and section.mimetype.search(mimetype):
                return section
            if section.regex and section.regex.search(path.as_posix()):
                return section
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


# singletone
class GlobalConfig:
    CONFIGS = ("extensions",)

    _extensions: ExtensionConfig

    def __init__(self) -> None:
        self._extensions = ExtensionConfig()

    @property
    def extensions(self) -> ExtensionConfig:
        return self._extensions

    def load_all_configs(self) -> None:
        for config_name in self.CONFIGS:
            self.__getattribute__(f"_{config_name}").load()

    def write_all_configs(self) -> None:
        for config_name in self.CONFIGS:
            self.__getattribute__(f"_{config_name}").write()


def get_config_file_path(config_filename: str) -> Path:
    config_file_path = _APP_CONFIG_DIR / f"{config_filename}"
    if not config_file_path.exists():
        shutil.copy(
            _DEFAULT_CONFIG_DIR / config_filename,
            config_file_path,
        )
    return config_file_path


GLOBAL_CONFIG = GlobalConfig()
