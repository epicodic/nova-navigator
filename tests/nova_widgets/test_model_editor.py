"""Unit tests for ModelEditor widget."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Select

from nova_navigator.config.model import BaseModel, field_comment
from nova_navigator.widgets.model_editor import ModelEditor
from nova_widgets import Checkbox, Input

# ── minimal test models ──────────────────────────────────────────────────────


class _Color(StrEnum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


@dataclass
class _ModelWithEnum(BaseModel):
    color: _Color = field(default=_Color.GREEN, metadata={"toml_comment": "A colour choice."})
    label: str = field_comment("hi", "A string label.")


@dataclass
class _FlatModel(BaseModel):
    flag: bool = field_comment(True, "A boolean flag.")
    name: str = field_comment("alice", "A string name.")
    count: int = field_comment(42, "An integer count.")
    ratio: float = field_comment(3.14, "A float ratio.")


@dataclass
class _NestedSection(BaseModel):
    value: int = field_comment(1, "Nested int.")


@dataclass
class _ModelWithNested(BaseModel):
    nested: _NestedSection = field(default_factory=_NestedSection)
    label: str = field_comment("top", "Top-level str.")


# ── test app wrapper ──────────────────────────────────────────────────────────


class _TestApp(App[None]):
    def __init__(self, editor: ModelEditor) -> None:
        super().__init__()
        self._editor = editor

    def compose(self) -> ComposeResult:
        yield self._editor


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bool_field_renders_checkbox() -> None:
    model = _FlatModel()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        assert len(list(editor.query(Checkbox))) == 1


@pytest.mark.asyncio
async def test_str_int_float_fields_render_inputs() -> None:
    model = _FlatModel()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        # str, int, float → 3 Input widgets
        assert len(list(editor.query(Input))) == 3


@pytest.mark.asyncio
async def test_initial_checkbox_value_matches_model() -> None:
    model = _FlatModel(flag=False)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        checkbox = editor.query_one(Checkbox)
        assert checkbox.value is False


@pytest.mark.asyncio
async def test_initial_input_values_match_model() -> None:
    model = _FlatModel(name="bob", count=7, ratio=2.5)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        inputs = list(editor.query(Input))
        values = {inp.value for inp in inputs}
        assert "bob" in values
        assert "7" in values
        assert "2.5" in values


@pytest.mark.asyncio
async def test_apply_writes_back_to_target() -> None:
    model = _FlatModel(flag=True, name="alice", count=1, ratio=1.0)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        editor.query_one(Checkbox).value = False
        for inp in editor.query(Input):
            if inp.value == "alice":
                inp.value = "new"
                break
        await pilot.pause()

        target = _FlatModel()
        editor.apply(target)
        assert target.flag is False
        assert target.name == "new"


@pytest.mark.asyncio
async def test_nested_base_model_field_not_rendered() -> None:
    """BaseModel sub-fields must not appear as rows."""
    model = _ModelWithNested()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        # Only 'label' (str) should be rendered — 'nested' must be skipped
        assert len(list(editor.query(Input))) == 1
        assert len(list(editor.query(Checkbox))) == 0


@pytest.mark.asyncio
async def test_apply_int_fallback_on_empty_input() -> None:
    """If numeric Input is emptied, apply() keeps the existing target value."""
    model = _FlatModel(count=99)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        for inp in editor.query(Input):
            if inp.value == "99":
                inp.value = ""
                break
        await pilot.pause()

        target = _FlatModel(count=99)
        editor.apply(target)
        assert target.count == 99


# ── enum field tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enum_field_renders_select() -> None:
    """Enum fields must produce a Select widget, not be silently skipped."""
    model = _ModelWithEnum()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        assert len(list(editor.query(Select))) == 1


@pytest.mark.asyncio
async def test_enum_select_has_all_members_as_options() -> None:
    """The Select widget must list every enum member."""
    model = _ModelWithEnum()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        sel = editor.query_one(Select)
        option_values = {value for _, value in sel._options if value is not Select.BLANK}  # type: ignore[attr-defined]
        assert option_values == {_Color.RED, _Color.GREEN, _Color.BLUE}


@pytest.mark.asyncio
async def test_enum_select_initial_value_matches_model() -> None:
    """Select must be pre-selected to the model's current enum value."""
    model = _ModelWithEnum(color=_Color.BLUE)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        sel = editor.query_one(Select)
        assert sel.value == _Color.BLUE


@pytest.mark.asyncio
async def test_enum_apply_writes_back_selected_value() -> None:
    """apply() must write the currently selected enum member back to the target."""
    model = _ModelWithEnum(color=_Color.GREEN)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        sel = editor.query_one(Select)
        sel.value = _Color.RED
        await pilot.pause()

        target = _ModelWithEnum()
        editor.apply(target)
        assert target.color is _Color.RED
