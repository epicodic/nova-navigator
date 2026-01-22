import sys
from pathlib import Path

# Redirect __pycache__ to avoid polluting the source tree.
# This mirrors the PYTHONPYCACHEPREFIX set by activate.sh, but takes effect
# for pytest runs even without sourcing activate.sh.
sys.pycache_prefix = str(Path(__file__).parent.parent / ".cache" / "pycache")

collect_ignore_glob = ["cleanup/*"]
# Common fixtures for all tests
