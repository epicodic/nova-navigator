"""Pure tests of the stable-symlink rule (ADR-1, REQ-4); no filesystems."""

import os
from pathlib import PurePosixPath

import pytest

from nova_navigator.filemanager.link_target import stable_link_target
from nova_navigator.filemanager.tasks import FileCopyOptions


def test_options_default_values() -> None:
    options = FileCopyOptions()
    assert options.follow_links is False
    assert options.stable_symlinks is False


def test_rule_req4_example() -> None:
    result = stable_link_target(
        "../../shared/x",
        PurePosixPath("/a/b/proj/doc/l"),
        PurePosixPath("/c/proj/doc/l"),
        PurePosixPath("/a/b/proj"),
    )
    assert result == "../../../a/b/shared/x"


def test_rule_inside_unchanged() -> None:
    result = stable_link_target(
        "../data.txt",
        PurePosixPath("/a/b/proj/doc/l"),
        PurePosixPath("/c/proj/doc/l"),
        PurePosixPath("/a/b/proj"),
    )
    assert result == "../data.txt"


def test_rule_target_equal_root_unchanged() -> None:
    result = stable_link_target(
        "..",
        PurePosixPath("/a/b/proj/doc/l"),
        PurePosixPath("/c/proj/doc/l"),
        PurePosixPath("/a/b/proj"),
    )
    assert result == ".."


def test_rule_sibling_with_same_prefix_is_outside() -> None:
    result = stable_link_target(
        "../../proj2/x",
        PurePosixPath("/a/b/proj/doc/l"),
        PurePosixPath("/c/proj/doc/l"),
        PurePosixPath("/a/b/proj"),
    )
    assert result == "../../../a/b/proj2/x"


def test_rule_root_slash_everything_inside() -> None:
    assert stable_link_target("../x", PurePosixPath("/a/l"), PurePosixPath("/c/l"), PurePosixPath("/")) == "../x"


@pytest.mark.parametrize("text", ["/etc/passwd", ""])
def test_rule_absolute_or_empty_unchanged(text: str) -> None:
    assert stable_link_target(text, PurePosixPath("/a/b/l"), PurePosixPath("/c/l"), PurePosixPath("/a")) == text


def test_rule_selected_link_root_none_is_outside() -> None:
    assert stable_link_target("f.txt", PurePosixPath("/a/d/l"), PurePosixPath("/c/l"), None) == "../a/d/f.txt"


def test_rule_selected_self_referencing_text_still_rewritten() -> None:
    assert stable_link_target("l", PurePosixPath("/a/d/l"), PurePosixPath("/c/l"), None) == "../a/d/l"


def test_rule_dotdot_beyond_root_clamped() -> None:
    assert stable_link_target("../../../../x", PurePosixPath("/a/l"), PurePosixPath("/c/l"), None) == "../x"


def test_rule_surrogate_text_roundtrip() -> None:
    result = stable_link_target("../s\udcffx", PurePosixPath("/a/b/l"), PurePosixPath("/c/l"), None)
    assert result == "../a/s\udcffx"
    assert os.fsencode(result) == b"../a/s\xffx"


def test_rule_trailing_slash_preserved() -> None:
    assert stable_link_target("../x/", PurePosixPath("/a/b/l"), PurePosixPath("/c/l"), None) == "../a/x/"


def test_rule_double_slash_prefix_collapsed() -> None:
    assert stable_link_target("../x", PurePosixPath("//a/b/l"), PurePosixPath("/c/l"), None) == "../a/x"


@pytest.mark.parametrize(
    ("source_link", "new_link", "root"),
    [
        (PurePosixPath("a/l"), PurePosixPath("/c/l"), None),
        (PurePosixPath("/a/l"), PurePosixPath("c/l"), None),
        (PurePosixPath("/a/l"), PurePosixPath("/c/l"), PurePosixPath("a")),
    ],
)
def test_rule_relative_input_raises(
    source_link: PurePosixPath,
    new_link: PurePosixPath,
    root: PurePosixPath | None,
) -> None:
    with pytest.raises(ValueError, match="absolute paths required"):
        stable_link_target("x", source_link, new_link, root)
