"""Stream a TAR archive into a new file, substituting one member."""

from __future__ import annotations

import copy
import tarfile
import time
from pathlib import Path
from typing import IO, Literal

_SUPPORTED_TYPES = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.FIFOTYPE,
    }
)


def rebuild_tar(source: Path, member: str, replacement: Path, output: Path) -> None:
    """Write *output* as *source* with *member* replaced by (or added from) *replacement*.

    The rebuilt archive keeps *source*'s compression (inferred from its filename), is written
    in PAX format, and reuses *source*'s global PAX headers. Every member's metadata (mode,
    ownership, mtime, name, link target, PAX headers) is preserved and unchanged file content
    is streamed through untouched; only *member*'s content is substituted from *replacement*.
    A tar member name with a leading "./" (as produced by e.g. `tar -C dir -cf x.tar .`) is
    treated as the same member as its unprefixed form, so "dir/edit.txt" also matches
    "./dir/edit.txt". If no member matches *member*, a new regular file entry is appended.

    Args:
        source: Path to the existing TAR archive to read.
        member: POSIX-style archive-relative name of the member to replace, or add if absent,
            without a leading slash.
        replacement: Path to a local file whose bytes become *member*'s new content.
        output: Path to write the rebuilt archive to; created exclusively ("xb").

    Raises:
        ValueError: If the archive contains metadata this module cannot safely reproduce (an
            ambiguous duplicate *member* name, a sparse member, an unsupported member type, a
            nonregular member with payload, a non-regular selected member, or changing global
            PAX headers).
        FileExistsError: If *output* already exists.
    """
    with output.open("xb") as destination:
        completed = False
        try:
            _rebuild_tar(source, member, replacement, destination)
            completed = True
        finally:
            if not completed:
                output.unlink(missing_ok=True)


def _tar_write_mode(source: Path) -> Literal["w:", "w:gz", "w:bz2", "w:xz"]:
    """Return the `tarfile` write mode matching *source*'s compression suffix."""
    if source.name.endswith((".tar.gz", ".tgz")):
        return "w:gz"
    if source.name.endswith((".tar.bz2", ".tbz2")):
        return "w:bz2"
    if source.name.endswith((".tar.xz", ".txz")):
        return "w:xz"
    return "w:"


def _member_names_match(name: str, member: str) -> bool:
    """Return whether tar member *name* refers to *member*.

    Archives built with a working-directory prefix (e.g. `tar -C dir -cf x.tar .`) name their
    entries with a leading "./", so "./dir/edit.txt" is treated as the same member as
    "dir/edit.txt".
    """
    return name in (member, f"./{member}")


def _check_selected(names: list[str], selected: str) -> None:
    """Raise ValueError if more than one archive member matches *selected*.

    *names* holds every member name that already matched *selected* (see
    `_member_names_match`). An empty *names* means no member matches, which `_rebuild_tar`
    treats as "append a new member" rather than an error.
    """
    if len(names) > 1:
        raise ValueError(f"Ambiguous duplicate archive member: {selected}")


def _append_member(rebuilt: tarfile.TarFile, member: str, replacement: Path) -> None:
    """Add *replacement*'s content as a brand-new regular member named *member*."""
    info = tarfile.TarInfo(member)
    info.size = replacement.stat().st_size
    info.mtime = int(time.time())
    info.mode = 0o644
    info.type = tarfile.REGTYPE
    with replacement.open("rb") as reader:
        rebuilt.addfile(info, reader)


def _rebuild_tar(source: Path, member: str, replacement: Path, output: IO[bytes]) -> None:
    """Stream *source* into *output*, replacing *member*'s content, or appending it."""
    with tarfile.open(source, "r:*") as original:
        initial_pax_headers = dict(original.pax_headers)
        members = original.getmembers()
        if original.pax_headers != initial_pax_headers:
            raise ValueError("Unsupported changing TAR global PAX metadata")
        matching_names = [info.name for info in members if _member_names_match(info.name, member)]
        _check_selected(matching_names, member)
        selected_name = matching_names[0] if matching_names else None
        for info in members:
            if info.issparse() or any(key.startswith("GNU.sparse") for key in info.pax_headers):
                raise ValueError("Unsupported sparse TAR member")
            if info.type not in _SUPPORTED_TYPES:
                raise ValueError("Unsupported TAR member type")
            if not info.isfile() and info.size != 0:
                raise ValueError("Unsupported nonregular TAR member payload")
            if info.name == selected_name and not info.isfile():
                raise ValueError("Selected TAR member must be a regular file")
        with tarfile.open(
            fileobj=output,
            mode=_tar_write_mode(source),
            format=tarfile.PAX_FORMAT,
            pax_headers=original.pax_headers,
        ) as rebuilt:
            for info in members:
                updated = copy.deepcopy(info)
                if info.name == selected_name:
                    updated.size = replacement.stat().st_size
                    if "size" in updated.pax_headers:
                        updated.pax_headers = {**updated.pax_headers, "size": str(updated.size)}
                    with replacement.open("rb") as reader:
                        rebuilt.addfile(updated, reader)
                elif info.isfile():
                    reader = original.extractfile(info)
                    if reader is None:
                        raise ValueError(f"Unreadable TAR member: {info.name}")
                    with reader:
                        rebuilt.addfile(updated, reader)
                else:
                    rebuilt.addfile(updated)
            if selected_name is None:
                _append_member(rebuilt, member, replacement)
