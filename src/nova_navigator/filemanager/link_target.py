"""Pure rule for the target text of a copied symbolic link ("Stable symlinks", ADR-1). No I/O."""

import posixpath
from pathlib import PurePosixPath


def _collapse_double_slash(path: str) -> str:
    """Reduce a leading ``//`` (kept by ``normpath``) to one slash."""
    return "/" + path.lstrip("/") if path.startswith("//") else path


def stable_link_target(
    text: str,
    source_link: PurePosixPath,
    new_link: PurePosixPath,
    source_root: PurePosixPath | None,
) -> str:
    """Return the target text for the copy of a link under the stable-symlink rule.

    All steps are lexical (``..`` is applied textually, links are not resolved).
    An absolute or empty *text* is returned unchanged. A relative *text* whose target lies at or below
    *source_root* is returned unchanged. Any other relative target is returned as the relative path
    from the directory of *new_link* to the same target path; a trailing slash of *text* is kept.
    *source_root* is ``None`` when the selected item is itself the link (its target always counts as outside).

    Raises:
        ValueError: *source_link*, *new_link* or *source_root* is not absolute (and *text* is relative).
    """
    if text == "" or text.startswith("/"):
        return text
    roots_absolute = source_root is None or source_root.is_absolute()
    if not (source_link.is_absolute() and new_link.is_absolute() and roots_absolute):
        raise ValueError(f"absolute paths required: {source_link}, {new_link}, {source_root}")
    source_dir = posixpath.dirname(source_link.as_posix())
    target = _collapse_double_slash(posixpath.normpath(posixpath.join(source_dir, text)))
    if source_root is not None:
        root = _collapse_double_slash(posixpath.normpath(source_root.as_posix()))
        if target == root or target.startswith(root.rstrip("/") + "/"):
            return text
    new = posixpath.relpath(target, posixpath.dirname(new_link.as_posix()))
    if text.endswith("/") and not new.endswith("/"):
        new += "/"
    return new
