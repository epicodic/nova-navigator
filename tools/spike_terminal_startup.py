"""Standalone spike: observe real zsh startup through the actual PtyBackend.

Runs /usr/bin/zsh exactly like the app (LocalPtyBackend + ZshDriver), writes the
init + editor-integration code, and prints every decoded recv_queue message with
timing so we can see whether pre_cmd / prompt_ready / editor ready+probe arrive.

Usage: uv run python tools/spike_terminal_startup.py [seconds] [command]
"""

from __future__ import annotations

import asyncio
import sys
import time

from nova_navigator.terminal.pty_backend import LocalPtyBackend
from nova_navigator.terminal.shell_driver import detect_driver


def _fmt(obj: object) -> str:
    if isinstance(obj, str):
        return repr(obj)
    return str(obj)


async def main() -> None:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 2.5
    command = sys.argv[2] if len(sys.argv) > 2 else "/usr/bin/zsh"
    driver = detect_driver(command)
    backend = LocalPtyBackend()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    backend.configure_editor_protocol("abc123def4567890")
    backend.open(command, 40, 120)
    backend.attach_readers(loop, queue)

    start = time.monotonic()
    startup_code = driver.init_code() + driver.editor_integration_code(
        "abc123def4567890"
    )
    print(f"# command={command!r} driver={type(driver).__name__}")
    print(f"# startup_code={len(startup_code)} bytes")
    backend.write(startup_code.encode())

    # Simulate the Terminal draining decision: drain from startup until the
    # first real prompt_ready (prompt-ready-capable drivers) so we can see what
    # the user would actually see on screen.
    draining = driver.supports_prompt_ready
    drained_bytes = 0
    visible_bytes = 0
    visible_chunks: list[str] = []
    counts: dict[str, int] = {}
    deadline = start + duration
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            break
        t = (time.monotonic() - start) * 1000
        cmd = str(msg[0])
        counts[cmd] = counts.get(cmd, 0) + 1
        if cmd == "stdout":
            payload = str(msg[1])
            if draining:
                drained_bytes += len(payload)
            else:
                visible_bytes += len(payload)
                visible_chunks.append(payload)
        else:
            print(f"[{t:7.1f}ms] {cmd}: {', '.join(_fmt(x) for x in msg[1:])}")
            if cmd == "prompt_ready" and draining:
                draining = False
                print(f"[{t:7.1f}ms] >>> draining ended on first prompt_ready")

    print("\n# --- summary ---")
    print(f"# message counts: {counts}")
    print(f"# drained (hidden) stdout bytes: {drained_bytes}")
    print(f"# visible stdout bytes: {visible_bytes}")
    print(f"# still draining at end: {draining}")
    print("# --- visible output (what the user sees) ---")
    print(_fmt("".join(visible_chunks)))
    backend.detach_readers()
    backend.teardown()


if __name__ == "__main__":
    asyncio.run(main())
