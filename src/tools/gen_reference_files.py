"""Generate deterministic reference files for the nova_editor large-file work.

Usage:
    uv run gen_reference_files normal --size 5GiB --seed 1 OUT
    uv run gen_reference_files longline --size 200MiB --seed 1 OUT
    uv run gen_reference_files all --out-dir DIR [--seed 1]

Output is a pure function of (mode, size, seed, GENERATOR_VERSION).
Memory use is bounded by the line pool plus one chunk.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

GENERATOR_VERSION = 1
CHUNK_BYTES = 1 << 20
MARKER_INTERVAL = CHUNK_BYTES
POOL_SIZE = 4096
MAX_ITEM_BYTES = 400
_BATCH_LINES = 32
_BATCH_ROOM = _BATCH_LINES * MAX_ITEM_BYTES

_ASCII = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789   .,;:-_/()[]{}"
_ACCENTED = "áéíóöüñçßøå"
_WIDE = "漢字日本語中文한국어"
_EMOJI = ("😀", "🚀", "🐍")
_COMBINING = ("e\u0301", "a\u0308", "n\u0303")
_EXTRA_SETS = (_ACCENTED, _WIDE, _EMOJI, _COMBINING)
_NON_ASCII_PER_MILLE = 100
_TAB_PER_MILLE = 50
_MASK64 = (1 << 64) - 1

_SIZE_RE = re.compile(r"^(\d+)\s*(B|KiB|MiB|GiB)?$", re.IGNORECASE)
_UNITS = {"b": 1, "kib": 1 << 10, "mib": 1 << 20, "gib": 1 << 30}


class _Rng:
    """SplitMix64 generator: tiny, fast, and stable across Python versions."""

    def __init__(self, seed: int) -> None:
        self._state = seed & _MASK64

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK64
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        return z ^ (z >> 31)

    def below(self, bound: int) -> int:
        """Return an integer in ``[0, bound)``."""
        return self.next_u64() % bound

    def between(self, low: int, high: int) -> int:
        """Return an integer in ``[low, high]``."""
        return low + self.below(high - low + 1)

    def chance(self, per_mille: int) -> bool:
        return self.below(1000) < per_mille

    def pick[T](self, items: Sequence[T]) -> T:
        return items[self.below(len(items))]


def parse_size(text: str) -> int:
    """Parse a size such as ``5GiB``, ``200MiB``, ``64KiB`` or a plain byte count."""
    match = _SIZE_RE.match(text.strip())
    if match is None:
        raise ValueError(f"invalid size: {text!r}")
    return int(match.group(1)) * _UNITS[(match.group(2) or "b").lower()]


def _make_line(rng: _Rng) -> str:
    target = rng.between(20, 200)
    parts: list[str] = []
    length = 0
    while length < target:
        part = "".join(rng.pick(_ASCII) for _ in range(rng.between(2, 9)))
        parts.append(part)
        length += len(part)
    if rng.chance(_NON_ASCII_PER_MILLE):
        for _ in range(rng.between(1, 3)):
            extra = rng.pick(rng.pick(_EXTRA_SETS))
            parts.insert(rng.below(len(parts) + 1), extra)
    if rng.chance(_TAB_PER_MILLE):
        for _ in range(rng.between(1, 3)):
            parts.insert(rng.below(len(parts) + 1), "\t")
    return "".join(parts)


def build_pool(rng: _Rng) -> list[bytes]:
    """Build the pool of line bodies (UTF-8, no line terminator, at most ``MAX_ITEM_BYTES - 1`` bytes)."""
    pool: list[bytes] = []
    while len(pool) < POOL_SIZE:
        encoded = _make_line(rng).encode("utf-8")
        if len(encoded) < MAX_ITEM_BYTES:
            pool.append(encoded)
    return pool


def _pad_normal(gap: int) -> bytes:
    return b"" if gap == 0 else b"x" * (gap - 1) + b"\n"


def _pad_longline(gap: int) -> bytes:
    return b"x" * gap


def _fill_chunk(
    rng: _Rng,
    items: list[bytes],
    prefix: bytes,
    target: int,
    pad: Callable[[int], bytes],
) -> bytes:
    """Return exactly ``target`` bytes made of ``prefix``, whole pool items, and an ASCII pad."""
    chunk = bytearray(prefix if len(prefix) <= target else b"")
    while target - len(chunk) >= _BATCH_ROOM:
        chunk += b"".join([rng.pick(items) for _ in range(_BATCH_LINES)])
    while True:
        item = rng.pick(items)
        if len(chunk) + len(item) > target:
            break
        chunk += item
    chunk += pad(target - len(chunk))
    return bytes(chunk)


def generate(mode: str, size: int, seed: int, path: Path, *, force: bool = False) -> float:
    """Write a reference file of exactly ``size`` bytes and return the elapsed seconds.

    Args:
        mode: ``"normal"`` (many lines, ends with a newline) or ``"longline"`` (one line, no newline).
        size: Exact file size in bytes.
        seed: Seed for the pseudo-random generator.
        path: Output file.
        force: Overwrite an existing file.
    """
    if mode not in ("normal", "longline"):
        raise ValueError(f"unknown mode: {mode!r}")
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; pass force to overwrite")
    started = time.perf_counter()
    rng = _Rng(seed)
    pool = build_pool(rng)
    if mode == "normal":
        items = [line + b"\n" for line in pool]
        pad = _pad_normal
    else:
        items = [line + b" " for line in pool]
        pad = _pad_longline
    written = 0
    block = 0
    with path.open("wb") as handle:
        while written < size:
            target = min(CHUNK_BYTES, size - written)
            prefix = f"#block {block}\n".encode() if mode == "normal" else f"<@{written}>".encode()
            handle.write(_fill_chunk(rng, items, prefix, target, pad))
            written += target
            block += 1
    return time.perf_counter() - started


def _size_arg(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, default_size in (("normal", "5GiB"), ("longline", "200MiB")):
        cmd = sub.add_parser(name, help=f"write one {name} reference file")
        cmd.add_argument("out", type=Path)
        cmd.add_argument("--size", type=_size_arg, default=parse_size(default_size))
        cmd.add_argument("--seed", type=int, default=1)
        cmd.add_argument("--force", action="store_true")
    both = sub.add_parser("all", help="write normal-5g.txt and longline-200m.txt")
    both.add_argument("--out-dir", type=Path, required=True)
    both.add_argument("--seed", type=int, default=1)
    both.add_argument("--normal-size", type=_size_arg, default=parse_size("5GiB"))
    both.add_argument("--longline-size", type=_size_arg, default=parse_size("200MiB"))
    both.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``gen_reference_files`` script."""
    args = _build_parser().parse_args(argv)
    jobs: list[tuple[str, int, Path]]
    if args.command == "all":
        args.out_dir.mkdir(parents=True, exist_ok=True)
        jobs = [
            ("normal", args.normal_size, args.out_dir / "normal-5g.txt"),
            ("longline", args.longline_size, args.out_dir / "longline-200m.txt"),
        ]
    else:
        jobs = [(args.command, args.size, args.out)]
    for mode, size, path in jobs:
        try:
            elapsed = generate(mode, size, args.seed, path, force=args.force)
        except FileExistsError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{path}: {size} bytes, mode={mode}, seed={args.seed}, {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
