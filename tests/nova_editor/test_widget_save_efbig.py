"""A real kernel write error (EFBIG through RLIMIT_FSIZE) during a save: the original stays, the temp file goes, the error is shown (REQ-12)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
LIMIT = 100_000
DATA = b"line of text for the real write error\n" * 8_000  # about 300 KB, well above LIMIT

CHILD = """
import asyncio, json, resource, signal, sys, time
from pathlib import Path

from nova_editor.app import NovaEditApp, SaveBar

path = Path(sys.argv[1])
limit = int(sys.argv[2])
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)


async def main() -> None:
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
        await pilot.press("ctrl+s")
        bar = app.query_one(SaveBar)
        deadline = time.monotonic() + 20
        while not bar.line.startswith("Save failed") and time.monotonic() < deadline:
            await pilot.pause(0.02)
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        print(json.dumps({"line": bar.line, "files": sorted(p.name for p in path.parent.iterdir())}))


asyncio.run(main())
"""


def test_a_real_efbig_leaves_the_original_removes_the_temp_file_and_shows_the_error(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_bytes(DATA)
    result = subprocess.run([sys.executable, "-c", CHILD, str(path), str(LIMIT)], capture_output=True, text=True, check=False, cwd=ROOT, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["line"].startswith("Save failed (")
    assert "File too large" in report["line"], report["line"]
    assert report["files"] == ["f.txt"]
    assert path.read_bytes() == DATA
