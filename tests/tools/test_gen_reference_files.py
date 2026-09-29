from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

from tools import gen_reference_files as gen


def test_parse_size() -> None:
    assert gen.parse_size("64KiB") == 65536
    assert gen.parse_size("5GiB") == 5 * 1024**3
    assert gen.parse_size("123") == 123
    with pytest.raises(ValueError, match="invalid size"):
        gen.parse_size("5 furlongs")


@pytest.mark.parametrize("mode", ["normal", "longline"])
@pytest.mark.parametrize("size", [0, 1, 5, 1000, gen.CHUNK_BYTES - 1, gen.CHUNK_BYTES, 2 * gen.CHUNK_BYTES + 12345])
def test_exact_size_and_valid_utf8(tmp_path: Path, mode: str, size: int) -> None:
    out = tmp_path / "f.txt"
    gen.generate(mode, size, 1, out)
    data = out.read_bytes()
    assert len(data) == size
    data.decode("utf-8")


@pytest.mark.parametrize("mode", ["normal", "longline"])
def test_deterministic(tmp_path: Path, mode: str) -> None:
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    gen.generate(mode, 300_000, 1, a)
    gen.generate(mode, 300_000, 1, b)
    gen.generate(mode, 300_000, 2, c)
    assert a.read_bytes() == b.read_bytes()
    assert a.read_bytes() != c.read_bytes()


def test_normal_structure(tmp_path: Path) -> None:
    out = tmp_path / "n.txt"
    gen.generate("normal", 3 * gen.CHUNK_BYTES, 1, out)
    data = out.read_bytes()
    text = data.decode("utf-8")
    assert data.endswith(b"\n")
    assert text.startswith("#block 0\n")
    assert "#block 1\n" in text
    assert "\t" in text
    assert any(ch in text for ch in gen._WIDE)
    assert any(ord(ch) > 0xFFFF for ch in text)
    assert any(ch in text for ch in gen._ACCENTED)
    assert any("̀" <= ch <= "ͯ" for ch in text)


def test_longline_structure(tmp_path: Path) -> None:
    out = tmp_path / "l.txt"
    gen.generate("longline", 3 * gen.CHUNK_BYTES, 1, out)
    data = out.read_bytes()
    assert b"\n" not in data
    assert not data.endswith(b"\n")
    assert data.startswith(b"<@0>")
    assert data[gen.MARKER_INTERVAL :].startswith(f"<@{gen.MARKER_INTERVAL}>".encode())


def test_streaming_memory_is_bounded(tmp_path: Path) -> None:
    tracemalloc.start()
    try:
        gen.generate("normal", 32 * gen.CHUNK_BYTES, 1, tmp_path / "m.txt")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16 * 1024 * 1024


def test_refuses_overwrite(tmp_path: Path) -> None:
    out = tmp_path / "o.txt"
    gen.generate("normal", 100, 1, out)
    with pytest.raises(FileExistsError):
        gen.generate("normal", 100, 1, out)
    gen.generate("normal", 200, 1, out, force=True)
    assert out.stat().st_size == 200


def test_combining_constants_are_decomposed() -> None:
    """Verify that every entry in _COMBINING is decomposed (length 2, second char is combining mark)."""
    for entry in gen._COMBINING:
        assert len(entry) == 2, f"Expected length 2, got {len(entry)} for {entry!r}"
        combining_char = entry[1]
        combining_code = ord(combining_char)
        assert 0x0300 <= combining_code <= 0x036F, f"Second character must be a combining mark (U+0300..U+036F), got U+{combining_code:04X} for {entry!r}"


def test_cli_all(tmp_path: Path) -> None:
    rc = gen.main(["all", "--out-dir", str(tmp_path), "--normal-size", "10KiB", "--longline-size", "20KiB"])
    assert rc == 0
    assert (tmp_path / "normal-5g.txt").stat().st_size == 10 * 1024
    assert (tmp_path / "longline-200m.txt").stat().st_size == 20 * 1024
    assert gen.main(["all", "--out-dir", str(tmp_path)]) == 1
