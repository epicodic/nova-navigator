import bz2
import gzip
import lzma
import os
from collections.abc import Callable
from pathlib import Path, PurePath

import pytest

from nova_navigator.archive.archive import Archive
from nova_navigator.archive.compressed_archive import CompressedFileArchive

_COMPRESSORS: dict[str, Callable[[bytes], bytes]] = {
    ".gz": gzip.compress,
    ".bz2": bz2.compress,
    ".xz": lzma.compress,
}
_SUFFIXES = list(_COMPRESSORS)
_ROOT = PurePath("/")


def _make(tmp_path: Path, name: str, content: bytes) -> CompressedFileArchive:
    suffix = next(s for s in _SUFFIXES if name.endswith(s))
    path = tmp_path / name
    path.write_bytes(_COMPRESSORS[suffix](content))
    return CompressedFileArchive(path, "r")


def _read_all(archive: CompressedFileArchive, path: PurePath, chunk: int = 7) -> bytes:
    reader = archive.read(path)
    parts: list[bytes] = []
    while data := reader.read(chunk):
        parts.append(data)
    reader.close()
    return b"".join(parts)


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_listdir_root_has_one_entry_without_suffix(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello")
    assert archive.listdir(_ROOT) == [PurePath("notes.txt")]


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_listdir_of_entry_is_empty(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello")
    assert archive.listdir(PurePath("/notes.txt")) == []


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_stats_of_entry(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello world")
    stat = archive.stats(PurePath("/notes.txt"))
    assert stat.size == 11
    assert not stat.is_directory
    assert not stat.is_hidden
    assert not stat.is_executable
    assert not stat.is_symlink
    assert stat.modified > 0


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_stats_of_root_is_directory(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello")
    assert archive.stats(_ROOT).is_directory


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_read_in_small_chunks(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello world, this is content")
    assert _read_all(archive, PurePath("/notes.txt")) == b"hello world, this is content"


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_read_multi_chunk_payload(tmp_path: Path, suffix: str) -> None:
    payload = (b"0123456789abcdef" * 4096 + b"tail") * 48  # about 3 MiB
    assert len(payload) > 3 * 1024 * 1024
    archive = _make(tmp_path, f"big{suffix}", payload)
    assert archive.stats(PurePath("/big")).size == len(payload)
    assert _read_all(archive, PurePath("/big"), chunk=65536) == payload


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_partial_read_does_not_materialise_everything(tmp_path: Path, suffix: str) -> None:
    payload = os.urandom(1024) * 3000
    archive = _make(tmp_path, f"big{suffix}", payload)
    reader = archive.read(PurePath("/big"))
    assert reader.read(10) == payload[:10]
    assert reader.read(10) == payload[10:20]
    reader.close()


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_each_read_is_an_independent_stream(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"abcdef")
    first = archive.read(PurePath("/notes.txt"))
    second = archive.read(PurePath("/notes.txt"))
    assert first.read(3) == b"abc"
    assert second.read(6) == b"abcdef"
    assert first.read(3) == b"def"
    first.close()
    second.close()


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_missing_path_raises_file_not_found(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello")
    with pytest.raises(FileNotFoundError):
        archive.stats(PurePath("/other"))
    with pytest.raises(FileNotFoundError):
        archive.read(PurePath("/other"))


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_read_root_raises_is_a_directory(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello")
    with pytest.raises(IsADirectoryError):
        archive.read(_ROOT)


@pytest.mark.parametrize("suffix", _SUFFIXES)
@pytest.mark.parametrize("mode", ["w", "a"])
def test_non_read_mode_is_refused(tmp_path: Path, suffix: str, mode: Archive.Mode) -> None:
    path = tmp_path / f"notes.txt{suffix}"
    path.write_bytes(_COMPRESSORS[suffix](b"hello"))
    with pytest.raises(PermissionError):
        CompressedFileArchive(path, mode)


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_suffix_only_name_uses_data_entry(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, suffix, b"hello")
    assert archive.listdir(_ROOT) == [PurePath("data")]


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_dotfile_entry_is_hidden(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f".bashrc{suffix}", b"hello")
    assert archive.listdir(_ROOT) == [PurePath(".bashrc")]
    assert archive.stats(PurePath("/.bashrc")).is_hidden


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_empty_file_raises_os_error(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"empty{suffix}"
    path.write_bytes(b"")
    with pytest.raises(OSError, match="Corrupt or empty"):
        CompressedFileArchive(path, "r")


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_garbage_raises_os_error(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"bad{suffix}"
    path.write_bytes(b"this is not compressed data at all" * 10)
    with pytest.raises(OSError, match="Corrupt or empty"):
        CompressedFileArchive(path, "r")


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_truncated_stream_raises_os_error(tmp_path: Path, suffix: str) -> None:
    compressed = _COMPRESSORS[suffix](os.urandom(20000))
    path = tmp_path / f"cut{suffix}"
    path.write_bytes(compressed[: len(compressed) // 2])
    with pytest.raises(OSError, match="Corrupt or empty"):
        CompressedFileArchive(path, "r")


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_missing_file_propagates_file_not_found(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(FileNotFoundError):
        CompressedFileArchive(tmp_path / f"nope{suffix}", "r")


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_close_is_idempotent(tmp_path: Path, suffix: str) -> None:
    archive = _make(tmp_path, f"notes.txt{suffix}", b"hello")
    archive.close()
    archive.close()


def test_multi_member_gzip_size_and_content(tmp_path: Path) -> None:
    path = tmp_path / "multi.gz"
    path.write_bytes(gzip.compress(b"first-") + gzip.compress(b"second"))
    archive = CompressedFileArchive(path, "r")
    assert archive.stats(PurePath("/multi")).size == len(b"first-second")
    assert _read_all(archive, PurePath("/multi")) == b"first-second"
