"""Shared tar archive builders for tests."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path


def build_tar_with_links(path: Path, *, link_before_target: bool = False) -> None:
    """Write a gzip tar with ``a.txt`` (content ``hello``), hard link ``b.txt`` -> ``a.txt`` and symlink ``s.txt`` -> ``a.txt``."""
    content = b"hello"

    def add_file(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("a.txt")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))

    def add_hard(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("b.txt")
        info.type = tarfile.LNKTYPE
        info.linkname = "a.txt"
        tar.addfile(info)

    with tarfile.open(path, mode="w:gz") as tar:
        if link_before_target:
            add_hard(tar)
            add_file(tar)
        else:
            add_file(tar)
            add_hard(tar)
        sym = tarfile.TarInfo("s.txt")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "a.txt"
        tar.addfile(sym)


def build_tar_with_symlink_tree(path: Path) -> None:
    """Write a gzip tar with ``real/`` (``real/f.txt`` = ``hello``) and a set of symlink members.

    Members: ``lnk`` -> ``real`` (directory link), ``flnk`` -> ``real/f.txt``, ``real/up`` -> ``..``,
    ``broken`` -> ``nope``, ``abs`` -> ``/etc/passwd``, ``esc`` -> ``../../x``, ``loop1`` <-> ``loop2``.
    """
    content = b"hello"
    with tarfile.open(path, mode="w:gz") as tar:
        directory = tarfile.TarInfo("real")
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        regular = tarfile.TarInfo("real/f.txt")
        regular.size = len(content)
        tar.addfile(regular, io.BytesIO(content))
        for name, target in (
            ("lnk", "real"),
            ("flnk", "real/f.txt"),
            ("real/up", ".."),
            ("broken", "nope"),
            ("abs", "/etc/passwd"),
            ("esc", "../../x"),
            ("loop1", "loop2"),
            ("loop2", "loop1"),
        ):
            sym = tarfile.TarInfo(name)
            sym.type = tarfile.SYMTYPE
            sym.linkname = target
            tar.addfile(sym)
