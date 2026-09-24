from __future__ import annotations

import logging

import pytest

from nova_navigator.commands import CommandMode
from nova_navigator.usermenu.evaluate import compile_menu, evaluate_menu
from nova_navigator.usermenu.model import MenuEntry


def _entry(
    entry_id: str,
    *,
    group: str | None = None,
    on: tuple[str, ...] = ("local",),
    when: str | None = None,
    default: str | bool = False,
    mode: CommandMode = CommandMode.TERMINAL,
) -> MenuEntry:
    return MenuEntry(id=entry_id, label=entry_id.upper(), run="true", group=group, on=on, when=when, default=default, mode=mode)


def _always(_mode: CommandMode) -> bool:
    return True


def _ids(rows: tuple[MenuEntry | None, ...]) -> list[str | None]:
    return [None if row is None else row.id for row in rows]


def test_syntax_error_disables_entry_and_reports() -> None:
    menu = compile_menu([_entry("bad", when="file and"), _entry("ok")])
    assert [c.entry.id for c in menu.entries] == ["ok"]
    assert len(menu.errors) == 1
    assert "'bad'" in menu.errors[0]
    assert "'when'" in menu.errors[0]


def test_when_filters_entries() -> None:
    menu = compile_menu([_entry("yes", when="flag"), _entry("no", when="not flag")])
    view = evaluate_menu(menu, {"flag": True}, "local", _always)
    assert _ids(view.rows) == ["yes"]


def test_scheme_must_be_listed_in_on() -> None:
    menu = compile_menu([_entry("local_only"), _entry("both", on=("local", "ssh"))])
    view = evaluate_menu(menu, {}, "ssh", _always)
    assert _ids(view.rows) == ["both"]


def test_can_run_filters_by_mode() -> None:
    menu = compile_menu([_entry("t"), _entry("b", mode=CommandMode.BACKGROUND)])
    view = evaluate_menu(menu, {}, "local", lambda mode: mode is CommandMode.TERMINAL)
    assert _ids(view.rows) == ["t"]


def test_when_error_hides_entry_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    menu = compile_menu([_entry("boom", when="file.is_file"), _entry("ok")])
    with caplog.at_level(logging.WARNING):
        view = evaluate_menu(menu, {"file": None}, "local", _always)
    assert _ids(view.rows) == ["ok"]
    assert "boom" in caplog.text


def test_restricted_builtins() -> None:
    menu = compile_menu([_entry("len_ok", when="len(items) == 2"), _entry("open_blocked", when="open('/etc/passwd')")])
    view = evaluate_menu(menu, {"items": [1, 2]}, "local", _always)
    assert _ids(view.rows) == ["len_ok"]


def test_generator_expression_sees_context_names() -> None:
    menu = compile_menu([_entry("gen", when="all(x < limit for x in items)")])
    view = evaluate_menu(menu, {"items": [1, 2], "limit": 3}, "local", _always)
    assert _ids(view.rows) == ["gen"]


def test_separators_between_groups() -> None:
    menu = compile_menu([_entry("a", group="A"), _entry("b", group="A"), _entry("c", group="C"), _entry("d")])
    view = evaluate_menu(menu, {}, "local", _always)
    assert _ids(view.rows) == ["a", "b", None, "c", None, "d"]


def test_hidden_entries_do_not_leave_double_separators() -> None:
    menu = compile_menu([_entry("a", group="A"), _entry("b", group="B", when="False"), _entry("c", group="C")])
    view = evaluate_menu(menu, {}, "local", _always)
    assert _ids(view.rows) == ["a", None, "c"]


def test_default_index_points_at_first_default() -> None:
    menu = compile_menu([_entry("a", group="A"), _entry("b", group="B", default="True"), _entry("c", default=True)])
    view = evaluate_menu(menu, {}, "local", _always)
    assert view.rows[view.default_index] is not None
    assert view.entry("b") is view.rows[view.default_index]


def test_default_error_counts_as_false() -> None:
    menu = compile_menu([_entry("a"), _entry("b", default="missing_name")])
    view = evaluate_menu(menu, {}, "local", _always)
    assert view.default_index == 0


def test_empty_view() -> None:
    view = evaluate_menu(compile_menu([_entry("a", when="False")]), {}, "local", _always)
    assert view.is_empty is True
