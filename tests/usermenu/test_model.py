from __future__ import annotations

import pytest

from nova_navigator.commands import CommandMode
from nova_navigator.usermenu.model import MenuConfigError, MenuEntry, MenuInput, parse_menu


def test_parse_minimal_entry_uses_defaults() -> None:
    [entry] = parse_menu('[make]\nlabel = "Make"\nrun = "make"\n')
    assert entry == MenuEntry(id="make", label="Make", run="make")
    assert entry.on == ("local",)
    assert entry.mode is CommandMode.TERMINAL
    assert entry.default is False


def test_parse_full_entry() -> None:
    text = """
[compress]
key = "c"
label = "Compress"
group = "Archives"
on = ["local", "ssh"]
when = "targets"
default = "True"
mode = "background"
run = "tar czf {archive} {targets}"

  [[compress.input]]
  name = "archive"
  prompt = "Archive name"
  default = "{dir.name}.tar.gz"
"""
    [entry] = parse_menu(text)
    assert entry.key == "c"
    assert entry.group == "Archives"
    assert entry.on == ("local", "ssh")
    assert entry.when == "targets"
    assert entry.default == "True"
    assert entry.mode is CommandMode.BACKGROUND
    assert entry.inputs == (MenuInput(name="archive", prompt="Archive name", default="{dir.name}.tar.gz"),)


def test_entries_keep_file_order() -> None:
    entries = parse_menu('[b]\nlabel = "B"\nrun = "b"\n[a]\nlabel = "A"\nrun = "a"\n')
    assert [e.id for e in entries] == ["b", "a"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[x\n", "Expected"),
        ("x = 1\n", "must be a table"),
        ('[x]\nrun = "r"\n', "'label'"),
        ('[x]\nlabel = "L"\n', "'run'"),
        ('[x]\nlabel = "L"\nrun = "r"\ncolour = "red"\n', "unknown field 'colour'"),
        ('[x]\nlabel = "L"\nrun = "r"\nkey = "ab"\n', "single visible character"),
        ('[x]\nlabel = "L"\nrun = "r"\nmode = "view"\n', "'mode'"),
        ('[x]\nlabel = "L"\nrun = "r"\non = "local"\n', "'on'"),
        ('[x]\nlabel = "L"\nrun = "r"\ndefault = 1\n', "'default'"),
        ('[x]\nlabel = "L"\nrun = "r"\n[[x.input]]\nname = "file"\nprompt = "P"\n', "reserved"),
        ('[x]\nlabel = "L"\nrun = "r"\n[[x.input]]\nname = "not valid"\nprompt = "P"\n', "identifier"),
        ('[x]\nlabel = "L"\nrun = "r"\n[[x.input]]\nname = "a"\nprompt = "P"\n[[x.input]]\nname = "a"\nprompt = "Q"\n', "duplicate"),
        ('[x]\nlabel = "L"\nrun = "r"\nmode = ""\n', "'mode'"),
        ('[x]\nlabel = "  "\nrun = "r"\n', "must not be empty"),
        ('[x]\nlabel = "L"\nrun = ""\n', "must not be empty"),
        ('[x]\nlabel = "L"\nrun = "r"\non = []\n', "must not be empty"),
        ('[x]\nlabel = "L"\nrun = "r"\nkey = " "\n', "single visible character"),
    ],
)
def test_invalid_config_raises(text: str, message: str) -> None:
    with pytest.raises(MenuConfigError, match=message):
        parse_menu(text)


def test_second_input_error_is_identified_by_index() -> None:
    text = '[x]\nlabel = "L"\nrun = "r"\n[[x.input]]\nname = "a"\nprompt = "P"\n[[x.input]]\nname = "not valid"\nprompt = "Q"\n'
    with pytest.raises(MenuConfigError, match="input #2"):
        parse_menu(text)
