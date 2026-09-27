"""Probe how an editor saves a file, as seen by ChangeDetector.

Usage: uv run local_copy_probe [--file PATH] [--settle S] [--poll S] -- EDITOR ARGS... (%f = file)

All probe output goes to stderr, so terminal editors can own the screen:

    uv run local_copy_probe -- nano %f 2> probe.log
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import shutil
import sys
import tempfile
import time
from pathlib import Path

from nova_navigator.vfs.change_detector import ChangeDetector, file_digest

_SAMPLE = "Line one\nLine two\nEdit and save me several times.\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="file to copy as the probe target (default: sample text)")
    parser.add_argument("--settle", type=float, default=1.0, help="ChangeDetector settle_time in seconds")
    parser.add_argument("--poll", type=float, default=1.0, help="ChangeDetector poll_interval in seconds")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="editor command, %%f is replaced by the file")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    work = Path(tempfile.mkdtemp(prefix="nn-probe-"))
    target = work / (args.file.name if args.file else "probe.txt")
    if args.file:
        await asyncio.to_thread(shutil.copyfile, args.file, target)
    else:
        await asyncio.to_thread(target.write_text, _SAMPLE)
    start = time.monotonic()
    changes = 0

    def log(kind: str, detail: str) -> None:
        print(f"{time.monotonic() - start:8.3f}  {kind:<10} {detail}", file=sys.stderr, flush=True)

    async def on_change(digest: str) -> None:
        nonlocal changes
        changes += 1
        log("SYNC", f"#{changes} would write back {digest[:12]}")

    baseline = await asyncio.to_thread(file_digest, target)
    detector = ChangeDetector(
        target,
        on_change,
        baseline_digest=baseline,
        poll_interval=args.poll,
        settle_time=args.settle,
        log=log,
    )
    await detector.start()
    try:
        command = [part.replace("%f", str(target)) for part in args.command if part != "--"]
        log("launch", " ".join(command))
        process = await asyncio.create_subprocess_exec(*command, cwd=work)
        try:
            code = await process.wait()
            log("exit", str(code))
        except asyncio.CancelledError:
            log("signal", "interrupted while the editor was running")
            raise
        await detector.check_now()
        print("Editor process exited. Keep editing in detached editors; press Ctrl+C to finish.", file=sys.stderr, flush=True)
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
    finally:
        await detector.stop()
        print(f"Reported changes: {changes}; probe directory: {work}", file=sys.stderr, flush=True)


def main() -> None:
    args = _parse_args()
    if not args.command:
        raise SystemExit("an editor command is required, e.g. -- vim %f")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
