from __future__ import annotations

from nova_navigator import debug_analytics
from nova_navigator.nova_navigator import NovaNavigator


def main() -> None:
    """Entry point for the Nova Navigator application."""
    NovaNavigator().run()
    debug_analytics.cleanup()


if __name__ == "__main__":
    main()
