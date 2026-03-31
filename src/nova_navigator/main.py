from __future__ import annotations

from nova_navigator import debug_analytics
from nova_navigator.nova_navigator import NovaNavigator
from nova_navigator.runtime_patches import apply_runtime_patches


def main() -> None:
    """Entry point for the Nova Navigator application."""
    apply_runtime_patches()
    NovaNavigator().run()
    debug_analytics.cleanup()


if __name__ == "__main__":
    main()
