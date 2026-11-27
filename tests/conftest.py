import sys
from collections.abc import Generator
from pathlib import Path

import pytest

from tests._utils.trace import restored_trace_function

# Redirect __pycache__ to avoid polluting the source tree.
# This mirrors the PYTHONPYCACHEPREFIX set by activate.sh, but takes effect
# for pytest runs even without sourcing activate.sh.
sys.pycache_prefix = str(Path(__file__).parent.parent / ".cache" / "pycache")

collect_ignore_glob = ["cleanup/*"]
# Common fixtures for all tests


@pytest.fixture(autouse=True)
def _restore_trace_function() -> Generator[None, None, None]:
    with restored_trace_function():
        yield
