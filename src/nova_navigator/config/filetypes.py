"""FileTypeConfig — maps file types to icons, colors, and open commands."""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import ClassVar

from nova_navigator.config.loader import ListConfig
from nova_navigator.config.model import BaseModel, computed, key_field
from nova_navigator.icons import ICONS
from nova_widgets import Icon


@dataclass
class Section(BaseModel):
    """A filetype section mapping patterns to display and open behaviour."""

    section_name: str = key_field()
    mimetype: str | None = None
    mimetype_pattern: re.Pattern[str] | None = computed(lambda s: re.compile(s.mimetype) if s.mimetype else None)  # noqa: RUF009
    regex: str | None = None
    regex_pattern: re.Pattern[str] | None = computed(lambda s: re.compile(s.regex) if s.regex else None)  # noqa: RUF009
    open: str | None = None
    open_cmd: list[str] | None = computed(lambda s: s.open.split() if s.open else None)  # noqa: RUF009
    color: str | None = None
    icon: str | None = None
    background_color: str | None = None


class FileTypeConfig(ListConfig):
    """Maps file types to icons, colors, and open commands."""

    CONFIG_NAME: ClassVar[str] = "filetypes"
    _item_cls: ClassVar[type[BaseModel]] = Section

    _sections: list[Section]
    _default_section: Section

    @classmethod
    def default_items(cls) -> list[BaseModel]:
        return [
            Section(section_name="default", open="xdg-open %f"),
            Section(section_name="videos", mimetype="video/.*", open="xdg-open %f", icon="video"),
            Section(section_name="images", mimetype="image/.*", icon="image"),
            Section(section_name="python", mimetype="text/x-python", icon="python"),
            Section(
                section_name="archives",
                mimetype="application/zip|application/x-tar|application/x-gzip|application/x-bzip2|application/java-archive",
                icon="archive",
            ),
            Section(section_name="pdf", mimetype="application/pdf", icon="pdf"),
            Section(section_name="deb", mimetype="application/vnd.debian.binary-package", icon="deb"),
            Section(section_name="audio", mimetype="audio/.*", icon="audio"),
            Section(section_name="cpp", mimetype="text/x-c\\+\\+src", icon="cpp"),
            Section(section_name="text", mimetype="text/.*", icon="text"),
            Section(section_name="model", mimetype="model/.*", icon="model"),
        ]

    @classmethod
    def load(cls) -> FileTypeConfig:
        instance: FileTypeConfig = super().load()  # type: ignore[assignment]
        instance._sections = instance._items  # type: ignore[assignment]

        default_section = next((s for s in instance._sections if s.section_name == "default"), None)
        if default_section is None:
            raise ValueError("Filetype config missing required [default] section")

        instance._default_section = default_section
        return instance

    def _replace_variables(self, cmd: list[str], path: PurePath) -> list[str]:
        cmd = [c.replace("%f", str(path)) for c in cmd]
        cmd = [c.replace("%d", str(path.parent)) for c in cmd]
        return cmd

    def _find_section_for_path(self, path: PurePath) -> Section:
        mimetype = mimetypes.guess_type(path.as_posix())[0]
        for section in self._sections:
            if section.mimetype and mimetype and section.mimetype_pattern is not None and section.mimetype_pattern.search(mimetype):
                return section
            if section.regex and section.regex_pattern is not None and section.regex_pattern.search(path.as_posix()):
                return section
        return self._default_section

    def get_open_command_for_file_path(self, path: PurePath) -> list[str]:
        section = self._find_section_for_path(path)
        open_cmd = section.open_cmd or self._default_section.open_cmd
        if open_cmd is None:
            raise RuntimeError("No open command found and default section has no open command")
        return self._replace_variables(open_cmd, path)

    def get_colors_for_filename(self, filename: str) -> tuple[str | None, str | None]:
        section = self._find_section_for_path(PurePath(filename))
        return section.color, section.background_color

    def get_icon_for_filename(self, filename: str, default: Icon | None = None) -> Icon:
        if default is None:
            default = Icon()
        section = self._find_section_for_path(PurePath(filename))
        if not section.icon:
            return default
        return ICONS.get_icon(section.icon, default=default)
