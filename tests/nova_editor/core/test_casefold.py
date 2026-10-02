"""Simple (1:1) case folding for literal search: classes, the static ASCII table and its cross-check."""

from __future__ import annotations

import pytest

from nova_editor.core.casefold import ascii_table, build_variant_table, fold1, variants


def test_static_ascii_table_equals_the_full_scan() -> None:
    derived = build_variant_table()
    static = ascii_table()
    for char, group in static.items():
        assert derived[char] == group
    assert {char for char in derived if char.isascii()} == set(static)


@pytest.mark.parametrize(
    ("char", "member"),
    [("k", "K"), ("s", "ſ"), ("σ", "ς"), ("µ", "μ"), ("ß", "ẞ"), ("é", "É")],
)
def test_named_classes(char: str, member: str) -> None:
    assert member in variants(char)
    assert char in variants(member)
    assert fold1(char) == fold1(member)


def test_sharp_s_does_not_match_ss() -> None:
    assert "s" not in variants("ß")
    assert variants("s") == ("S", "s", "ſ")


def test_dotted_and_dotless_i_match_only_themselves() -> None:
    assert variants("İ") == ("İ",)
    assert variants("ı") == ("ı",)
    assert variants("i") == ("I", "i")


def test_classes_are_symmetric_and_share_fold() -> None:
    for group in set(build_variant_table().values()):
        assert list(group) == sorted(group)
        for member in group:
            assert variants(member) == group
            assert fold1(member) == fold1(group[0])


def test_fold1_is_idempotent_on_the_class_members() -> None:
    for group in set(build_variant_table().values()):
        for member in group:
            assert fold1(fold1(member)) == fold1(member)


def test_escaped_bytes_and_non_letters_are_alone() -> None:
    assert variants("\udc80") == ("\udc80",)
    assert variants("1") == ("1",)
    assert variants("\n") == ("\n",)
    assert variants("\U0001f600") == ("\U0001f600",)
