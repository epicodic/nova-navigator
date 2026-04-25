"""Shared test setup for DirectoryBrowser widget tests."""

import pytest

from nova_navigator.config import conf_, get_config_file_path
from nova_navigator.icons import ICONS, IconSet


@pytest.fixture(autouse=True, scope="session")
def initialize_app_globals() -> None:
    """Initialize the global config and icon set, as main() normally does."""
    conf_.load_all_configs()
    ICONS.load_icons(get_config_file_path("icons.csv"))
    ICONS.set_variant(IconSet.Variants.NERDFONT)
