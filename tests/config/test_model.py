from dataclasses import dataclass, field

import pytest
import tomlkit

from nova_navigator.config.model import (
    BaseModel,
    ConfigLoadError,
    computed,
    from_toml,
    key_field,
    list_from_toml,
    list_to_toml,
    to_toml,
    update_toml_doc,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@dataclass
class Flat(BaseModel):
    """A flat config with simple fields."""

    name: str = "default"
    count: int = 0
    active: bool = False
    label: str | None = None


@dataclass
class WithComputed(BaseModel):
    value: str = "hello"
    upper: str = computed(lambda s: s.value.upper())


@dataclass
class WithKey(BaseModel):
    section_name: str = key_field()
    mimetype: str | None = None
    icon: str | None = None


@dataclass
class Nested(BaseModel):
    """Outer config."""

    inner: Flat = field(default_factory=Flat)
    title: str = "outer"


@dataclass
class WithList(BaseModel):
    items: list[Flat] = field(default_factory=list)


# ── ConfigModel + computed ────────────────────────────────────────────────────


def test_computed_field_runs_after_init() -> None:
    obj = WithComputed(value="world")
    assert obj.upper == "WORLD"


def test_computed_field_uses_default() -> None:
    obj = WithComputed()
    assert obj.upper == "HELLO"


def test_computed_field_not_in_dataclass_init() -> None:
    # upper has init=False so passing it to the constructor raises TypeError
    with pytest.raises(TypeError):
        WithComputed(value="x", upper="ignored")  # type: ignore[call-arg]


def test_key_field_default_is_empty_string() -> None:
    obj = WithKey()
    assert obj.section_name == ""


# ── from_toml: scalars ────────────────────────────────────────────────────────


def test_from_toml_reads_scalar_fields() -> None:
    t = tomlkit.loads('name = "custom"\ncount = 42\nactive = true')
    obj = from_toml(Flat, t)
    assert obj.name == "custom"
    assert obj.count == 42
    assert obj.active is True


def test_from_toml_uses_dataclass_defaults_for_missing_fields() -> None:
    t = tomlkit.loads("")
    obj = from_toml(Flat, t)
    assert obj.name == "default"
    assert obj.count == 0


def test_from_toml_optional_field_absent_is_none() -> None:
    t = tomlkit.loads('name = "x"')
    obj = from_toml(Flat, t)
    assert obj.label is None


def test_from_toml_optional_field_present_is_read() -> None:
    t = tomlkit.loads('label = "hello"')
    obj = from_toml(Flat, t)
    assert obj.label == "hello"


def test_from_toml_missing_required_field_raises_config_load_error() -> None:
    @dataclass
    class Required(BaseModel):
        must_have: int

    t = tomlkit.loads("")
    with pytest.raises(ConfigLoadError) as exc_info:
        from_toml(Required, t)
    assert "must_have" in str(exc_info.value)


def test_from_toml_unknown_field_in_toml_is_ignored() -> None:
    t = tomlkit.loads('name = "x"\nunknown_key = 99')
    obj = from_toml(Flat, t)  # must not raise
    assert obj.name == "x"


# ── from_toml: nested ConfigModel ─────────────────────────────────────────────


def test_from_toml_nested_config_model() -> None:
    t = tomlkit.loads('[inner]\nname = "inner_val"\ncount = 7')
    obj = from_toml(Nested, t)
    assert obj.inner.name == "inner_val"
    assert obj.inner.count == 7
    assert obj.title == "outer"


# ── from_toml: list[ConfigModel] ──────────────────────────────────────────────


def test_from_toml_list_of_config_model() -> None:
    t = tomlkit.loads("[[items]]\nname = 'a'\n\n[[items]]\nname = 'b'")
    obj = from_toml(WithList, t)
    assert len(obj.items) == 2
    assert obj.items[0].name == "a"
    assert obj.items[1].name == "b"


# ── from_toml: key_field ──────────────────────────────────────────────────────


def test_from_toml_key_field_set_from_key_argument() -> None:
    t = tomlkit.loads('icon = "video"')
    obj = from_toml(WithKey, t, key="videos")
    assert obj.section_name == "videos"
    assert obj.icon == "video"


# ── to_toml ───────────────────────────────────────────────────────────────────


def test_to_toml_produces_scalar_fields() -> None:
    obj = Flat(name="test", count=5, active=True)
    doc = to_toml(obj)
    assert doc["name"] == "test"
    assert doc["count"] == 5
    assert doc["active"] is True


def test_to_toml_skips_computed_fields() -> None:
    obj = WithComputed(value="abc")
    doc = to_toml(obj)
    assert "upper" not in doc
    assert "value" in doc


def test_to_toml_skips_key_fields() -> None:
    obj = WithKey(section_name="videos", icon="video")
    doc = to_toml(obj)
    assert "section_name" not in doc
    assert "icon" in doc


def test_to_toml_skips_none_optional_fields() -> None:
    obj = Flat(label=None)
    doc = to_toml(obj)
    assert "label" not in doc


def test_to_toml_includes_docstring_as_comment() -> None:
    obj = Flat()
    serialised = tomlkit.dumps(to_toml(obj))
    assert "# A flat config with simple fields." in serialised


def test_to_toml_no_comment_when_no_docstring() -> None:
    @dataclass
    class NoDoc(BaseModel):
        value: int = 0

    serialised = tomlkit.dumps(to_toml(NoDoc()))
    assert "#" not in serialised


def test_to_toml_nested_config_model_produces_subtable() -> None:
    obj = Nested(title="top")
    doc = to_toml(obj)
    assert "inner" in doc
    assert doc["inner"]["name"] == "default"  # type: ignore


def test_to_toml_list_of_config_model_produces_aot() -> None:
    obj = WithList(items=[Flat(name="a"), Flat(name="b")])
    doc = to_toml(obj)
    assert "items" in doc
    serialised = tomlkit.dumps(doc)
    assert "[[items]]" in serialised


# ── round-trip ────────────────────────────────────────────────────────────────


def test_round_trip_scalar_fields() -> None:
    original = Flat(name="round", count=99, active=True, label="yes")
    doc = to_toml(original)
    restored = from_toml(Flat, doc)
    assert restored.name == "round"
    assert restored.count == 99
    assert restored.active is True
    assert restored.label == "yes"


def test_round_trip_nested() -> None:
    original = Nested(inner=Flat(name="deep"), title="up")
    doc = to_toml(original)
    restored = from_toml(Nested, doc)
    assert restored.inner.name == "deep"
    assert restored.title == "up"


# ── list_from_toml / list_to_toml ─────────────────────────────────────────────


def test_list_from_toml_creates_instances_with_key() -> None:
    doc = tomlkit.loads('[videos]\nicon = "video"\n\n[default]\nicon = "file"')
    items = list_from_toml(WithKey, doc)
    assert len(items) == 2
    assert items[0].section_name == "videos"
    assert items[0].icon == "video"
    assert items[1].section_name == "default"


def test_list_to_toml_uses_key_field_as_section_heading() -> None:
    items = [
        WithKey(section_name="videos", icon="video"),
        WithKey(section_name="default", icon="file"),
    ]
    doc = list_to_toml(items)
    serialised = tomlkit.dumps(doc)
    assert "[videos]" in serialised
    assert "[default]" in serialised
    assert "video" in serialised


def test_list_round_trip() -> None:
    original = [
        WithKey(section_name="videos", icon="video"),
        WithKey(section_name="default"),
    ]
    doc = list_to_toml(original)
    restored = list_from_toml(WithKey, doc)
    assert len(restored) == 2
    assert restored[0].section_name == "videos"
    assert restored[0].icon == "video"
    assert restored[1].section_name == "default"
    assert restored[1].icon is None


# ── update_toml_doc ────────────────────────────────────────────────────────────


def test_update_toml_doc_preserves_comments() -> None:
    original_toml = "# my comment\nname = 'old'\ncount = 0\n"
    doc = tomlkit.loads(original_toml)
    obj = Flat(name="new", count=5)
    update_toml_doc(doc, obj)
    serialised = tomlkit.dumps(doc)
    assert "# my comment" in serialised
    assert "new" in serialised
    assert "5" in serialised


def test_update_toml_doc_updates_nested_scalars() -> None:
    original_toml = "[inner]\nname = 'old'\ncount = 0\ntitle = 'outer'\n"
    doc = tomlkit.loads(original_toml)
    obj = Nested(inner=Flat(name="new"), title="updated")
    update_toml_doc(doc, obj)
    assert doc["inner"]["name"] == "new"  # type: ignore
    assert doc["title"] == "updated"


def test_from_toml_file_path_in_error_message() -> None:
    @dataclass
    class Required(BaseModel):
        must_have: int

    t = tomlkit.loads("")
    with pytest.raises(ConfigLoadError) as exc_info:
        from_toml(Required, t, file_path="/some/path/config.toml")
    assert "/some/path/config.toml" in str(exc_info.value)


def test_update_toml_doc_absent_nested_key_does_not_raise() -> None:
    # update_toml_doc should silently skip nested tables not present in doc
    original_toml = "title = 'outer'\n"  # no [inner] section
    doc = tomlkit.loads(original_toml)
    obj = Nested(inner=Flat(name="new"), title="updated")
    update_toml_doc(doc, obj)  # must not raise
    assert doc["title"] == "updated"


def test_update_toml_doc_skips_list_config_model_fields() -> None:
    original_toml = "title = 'outer'\n"
    doc = tomlkit.loads(original_toml)
    obj = WithList(items=[Flat(name="a"), Flat(name="b")])
    update_toml_doc(doc, obj)  # must not raise


def test_update_toml_doc_adds_missing_nested_section() -> None:
    # When a nested section is absent from the file (e.g. added after first install),
    # update_toml_doc must add it so the values are persisted.
    original_toml = "title = 'outer'\n"  # no [inner] section
    doc = tomlkit.loads(original_toml)
    obj = Nested(inner=Flat(name="new", count=7), title="updated")
    update_toml_doc(doc, obj)
    assert doc["inner"]["name"] == "new"  # type: ignore
    assert doc["inner"]["count"] == 7  # type: ignore


def test_update_toml_doc_serializes_enum_as_string() -> None:
    from dataclasses import dataclass
    from enum import StrEnum

    class Color(StrEnum):
        RED = "red"
        BLUE = "blue"

    @dataclass
    class WithEnum(BaseModel):
        color: Color = Color.RED

    doc = tomlkit.loads('color = "red"\n')
    obj = WithEnum(color=Color.BLUE)
    update_toml_doc(doc, obj)
    assert doc["color"] == "blue"


# ── Enum fields ───────────────────────────────────────────────────────────────


def test_from_toml_deserializes_enum_field_from_string() -> None:
    from dataclasses import dataclass
    from enum import StrEnum

    class Color(StrEnum):
        RED = "red"
        BLUE = "blue"

    @dataclass
    class WithEnum(BaseModel):
        color: Color = Color.RED

    doc = tomlkit.loads('color = "blue"')
    obj = from_toml(WithEnum, doc)
    assert obj.color is Color.BLUE


def test_to_toml_serializes_enum_field_as_string() -> None:
    from dataclasses import dataclass
    from enum import StrEnum

    class Color(StrEnum):
        RED = "red"
        BLUE = "blue"

    @dataclass
    class WithEnum(BaseModel):
        color: Color = Color.RED

    obj = WithEnum(color=Color.BLUE)
    serialised = tomlkit.dumps(to_toml(obj))
    assert '"blue"' in serialised or "blue" in serialised


def test_enum_field_round_trips_through_toml() -> None:
    from dataclasses import dataclass
    from enum import StrEnum

    class Color(StrEnum):
        RED = "red"
        BLUE = "blue"

    @dataclass
    class WithEnum(BaseModel):
        color: Color = Color.RED

    original = WithEnum(color=Color.BLUE)
    doc = to_toml(original)
    restored = from_toml(WithEnum, doc)
    assert restored.color is Color.BLUE
