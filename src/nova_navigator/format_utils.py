"""Shared formatting utilities."""

from nova_navigator.config.global_config import conf_

_DECIMAL_MAGNITUDE: int = 1000
_BINARY_MAGNITUDE: int = 1024
_DECIMAL_UNITS: tuple[str, ...] = (" B", "KB", "MB", "GB", "TB")
_BINARY_UNITS: tuple[str, ...] = ("  B", "KiB", "MiB", "GiB", "TiB")


def format_size(size: int) -> str:
    """Format *size* bytes using the size format configured in settings.

    Uses decimal (base-1000) or binary (base-1024) magnitudes depending on
    ``general.use_binary_sizes`` in the application settings.
    """
    if conf_.settings.general.use_binary_sizes:
        magnitude, units = _BINARY_MAGNITUDE, _BINARY_UNITS
    else:
        magnitude, units = _DECIMAL_MAGNITUDE, _DECIMAL_UNITS
    value: float = size
    for unit in units:
        if value < magnitude:
            if unit == units[0]:
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= magnitude
    return f"{value:.1f} {units[-1]}"
