from __future__ import annotations

from dataclasses import dataclass

import pytest

from nova_navigator.usermenu.template import PlaceholderError, expand


@dataclass
class _Item:
    name: str
    path: str

    def __str__(self) -> str:
        return self.path


_A = _Item("a b.txt", "/d/a b.txt")
_B = _Item("c.txt", "/d/c.txt")
_NAMES: dict[str, object] = {"file": _A, "targets": [_A, _B], "archive": "x y.tgz", "empty": [], "nothing": None}


def test_value_is_quoted() -> None:
    assert expand("tar czf {archive}", _NAMES) == "tar czf 'x y.tgz'"


def test_object_expands_to_str() -> None:
    assert expand("cat {file}", _NAMES) == "cat '/d/a b.txt'"


def test_attribute_path() -> None:
    assert expand("echo {file.name}", _NAMES) == "echo 'a b.txt'"


def test_list_expands_to_quoted_items() -> None:
    assert expand("ls {targets}", _NAMES) == "ls '/d/a b.txt' /d/c.txt"


def test_attribute_maps_over_list() -> None:
    assert expand("ls {targets.name}", _NAMES) == "ls 'a b.txt' c.txt"


def test_raw_skips_quoting() -> None:
    assert expand("echo {archive:raw}", _NAMES) == "echo x y.tgz"


def test_quote_false_skips_quoting() -> None:
    assert expand("{archive}", _NAMES, quote=False) == "x y.tgz"


def test_empty_list_expands_to_nothing() -> None:
    assert expand("ls {empty}", _NAMES) == "ls "


def test_shell_variable_is_untouched() -> None:
    assert expand("echo ${file} $HOME", _NAMES) == "echo ${file} $HOME"


def test_unknown_name_is_left_unchanged() -> None:
    assert expand("awk '{print $1}' {unknown}", _NAMES) == "awk '{print $1}' {unknown}"


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("{file.nope}", "no attribute 'nope'"),
        ("{file._secret}", "private attribute"),
        ("{nothing}", "has no value"),
        ("{file.__str__}", "private attribute"),
    ],
)
def test_errors(template: str, message: str) -> None:
    with pytest.raises(PlaceholderError, match=message):
        expand(template, _NAMES)


def test_callable_value_is_an_error() -> None:
    with pytest.raises(PlaceholderError, match="is not a value"):
        expand("{f}", {"f": len})
