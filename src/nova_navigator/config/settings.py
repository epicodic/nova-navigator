"""Settings config for Nova Navigator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from nova_navigator.config.loader import ModelConfig
from nova_navigator.config.model import BaseModel, field_comment


class NerdFontMode(StrEnum):
    """NerdFont icon rendering mode."""

    YES = "yes"
    NO = "no"
    AUTO = "auto"


@dataclass
class GeneralSettings(BaseModel):
    """General application settings."""

    show_hidden_files: bool = field_comment(False, "Show hidden files in the file browser.")
    confirm_delete: bool = field_comment(True, "Ask for confirmation before deleting files.")
    use_binary_sizes: bool = field_comment(
        False, "Use binary (base-1024) size magnitudes instead of decimal (base-1000)."
    )
    use_nerd_font: NerdFontMode = field(
        default=NerdFontMode.AUTO,
        metadata={"toml_comment": "NerdFont icon rendering: yes (always on), no (always off), auto (detect)."},
    )


@dataclass
class NetworkSettings(BaseModel):
    """Network settings."""

    ssh_timeout: int = field_comment(30, "SSH connection timeout in seconds.")
    proxy: str = field_comment("", "HTTP proxy URL, e.g. http://proxy:3128. Leave empty to disable.")


@dataclass
class Settings(BaseModel, ModelConfig):
    """Application settings."""

    CONFIG_NAME: ClassVar[str] = "settings"
    general: GeneralSettings = field(default_factory=GeneralSettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
