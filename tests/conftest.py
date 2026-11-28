import sys
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest

from nova_navigator import session_state
from nova_navigator.config import loader
from nova_navigator.secret_registry import clear_secrets
from tests._utils.trace import restored_trace_function

# Redirect __pycache__ to avoid polluting the source tree.
# This mirrors the PYTHONPYCACHEPREFIX set by activate.sh, but takes effect
# for pytest runs even without sourcing activate.sh.
sys.pycache_prefix = str(Path(__file__).parent.parent / ".cache" / "pycache")

collect_ignore_glob = ["cleanup/*"]
# Common fixtures for all tests


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_dirs_for_session(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point user config and state at a temporary directory for session-scoped setup."""
    with pytest.MonkeyPatch.context() as patch:
        root = tmp_path_factory.mktemp("user")
        patch.setattr(loader, "_APP_CONFIG_DIR", root / ".config" / "nova-navigator")
        patch.setattr(session_state, "_STATE_FILE", root / "state" / "state.toml")
        yield


@pytest.fixture(autouse=True)
def _isolate_user_dirs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point user config and state at a fresh temporary directory for each test."""
    root = tmp_path_factory.mktemp("user")
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", root / ".config" / "nova-navigator")
    monkeypatch.setattr(session_state, "_STATE_FILE", root / "state" / "state.toml")


@pytest.fixture(autouse=True)
def _restore_trace_function() -> Generator[None, None, None]:
    with restored_trace_function():
        yield


@pytest.fixture(autouse=True)
def _clear_registered_secrets() -> Generator[None, None, None]:
    """Keep secrets registered by one test out of the next."""
    clear_secrets()
    yield
    clear_secrets()
