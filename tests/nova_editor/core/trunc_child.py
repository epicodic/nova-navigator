"""Child of the truncation test: open, announce READY, wait for the parent's truncation, then read the old tail."""

from __future__ import annotations

import sys

from nova_editor.core.byte_source import PreadSource, SourceChanged


class NoStatSource(PreadSource):
    """Skips the fstat check so that only the short-read rule can detect the truncation."""

    def _check_stat(self) -> None:
        return


def main() -> int:
    mode, path = sys.argv[1], sys.argv[2]
    source = NoStatSource(path) if mode == "short-read-only" else PreadSource(path)
    print("READY", flush=True)
    sys.stdin.readline()
    try:
        data = source.read(source.length() - 4096, 4096)
    except SourceChanged:
        print("SOURCECHANGED", flush=True)
        return 0
    print(f"OK {len(data)} bytes", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
