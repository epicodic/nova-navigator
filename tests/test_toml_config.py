import pytest
import tomlkit

from nova_navigator.toml_config import Field, TomlConfig

# ── Shared config classes ─────────────────────────────────────────────────────


class NestedConfig(TomlConfig):
    field1: str = Field(default="nested_default")
    field2: int = Field(default=0)


class FlatConfig(TomlConfig):
    """Config with only basic-type fields, all with defaults."""

    field1: str = Field(default="default_str")
    field2: int = Field(default=0)
    field3: list[int] = Field(default=[])


class NestedParentConfig(TomlConfig):
    """Config with a required nested sub-config."""

    nested: NestedConfig = Field()


class ArrayConfig(TomlConfig):
    array: list[NestedConfig] = Field(default=[])


# ── Basic field loading ───────────────────────────────────────────────────────


def test_basic_fields_read_from_toml() -> None:
    t = tomlkit.loads("""
    field1 = "custom_value"
    field2 = 42
    field3 = [1, 2, 3]
    """)

    obj = FlatConfig(t)
    assert isinstance(obj.field1, str)
    assert obj.field1 == "custom_value"
    assert isinstance(obj.field2, int)
    assert obj.field2 == 42
    assert isinstance(obj.field3, list)
    assert obj.field3 == [1, 2, 3]
    assert isinstance(obj.field3[0], int)


def test_default_values_used_when_fields_absent() -> None:
    t = tomlkit.loads("")

    obj = FlatConfig(t)
    assert obj.field1 == "default_str"
    assert obj.field2 == 0
    assert obj.field3 == []


def test_nested_config_read_from_toml() -> None:
    t = tomlkit.loads("""
    [nested]
    field1 = "nested_value"
    field2 = 100
    """)

    obj = NestedParentConfig(t)
    assert isinstance(obj.nested, NestedConfig)
    assert obj.nested.field1 == "nested_value"
    assert obj.nested.field2 == 100


def test_array_of_configs_read_from_toml() -> None:
    t = tomlkit.loads("""
    [[array]]
    field1 = "item1"
    field2 = 10

    [[array]]
    field1 = "item2"
    field2 = 20

    [[array]]
    field1 = "item3"
    field2 = 30
    """)

    obj = ArrayConfig(t)
    assert isinstance(obj.array, list)
    assert len(obj.array) == 3
    for i, item in enumerate(obj.array):
        assert isinstance(item, NestedConfig)
        assert item.field1 == f"item{i + 1}"
        assert item.field2 == (i + 1) * 10


# ── Optional fields ───────────────────────────────────────────────────────────


def test_optional_field_absent_from_toml_is_none() -> None:
    class ConfigWithOptional(TomlConfig):
        present: str = Field(default="value")
        absent: str | None

    t = tomlkit.loads('present = "hello"')

    obj = ConfigWithOptional(toml=t)
    assert obj.present == "hello"
    assert obj.absent is None


def test_optional_field_present_in_toml_is_read() -> None:
    class ConfigWithOptional(TomlConfig):
        field: str | None

    t = tomlkit.loads('field = "provided"')

    obj = ConfigWithOptional(toml=t)
    assert obj.field == "provided"


# ── Default factory ───────────────────────────────────────────────────────────


def test_default_factory_field() -> None:
    class ConfigWithDefaultFactory(TomlConfig):
        field1: list[int] = Field(default_factory=lambda _: [])
        field2: dict[str, str] = Field(default_factory=lambda _: {})
        field3: str = Field(default="hello")
        # field4 references field3; fields are processed in declaration order
        # so field3 is guaranteed to be initialised before field4's factory runs
        field4: str | None = Field(default_factory=lambda data: data.field3.upper())

    t = tomlkit.loads("")

    obj = ConfigWithDefaultFactory(toml=t)
    assert obj.field1 == []
    assert obj.field2 == {}
    assert obj.field3 == "hello"
    assert obj.field4 == "HELLO"


# ── Error cases ───────────────────────────────────────────────────────────────


def test_missing_required_field_raises_value_error() -> None:
    class RequiredFieldConfig(TomlConfig):
        required: int

    t = tomlkit.loads("")

    with pytest.raises(ValueError, match="Missing field 'required'"):
        RequiredFieldConfig(toml=t)


def test_unknown_kwarg_raises_value_error() -> None:
    t = tomlkit.loads("")

    with pytest.raises(ValueError, match="Unknown field"):
        FlatConfig(toml=t, not_a_field="value")


def test_list_field_with_non_array_toml_type_raises_type_error() -> None:
    t = tomlkit.loads('field3 = "not_a_list"')

    with pytest.raises(TypeError, match="expected to be a TOML array"):
        FlatConfig(toml=t)


def test_non_field_info_class_attribute_raises_type_error() -> None:
    class BadConfig(TomlConfig):
        field1: str = "plain_string_not_field_info"  # type: ignore[assignment]

    t = tomlkit.loads('field1 = "value"')

    with pytest.raises(TypeError, match="is not a FieldInfo instance"):
        BadConfig(toml=t)


# ── Exclude ───────────────────────────────────────────────────────────────────


def test_exclude_field_ignores_toml_value() -> None:
    class ConfigWithExclude(TomlConfig):
        computed: str = Field(default="from_default", exclude=True)

    t = tomlkit.loads('computed = "from_toml"')

    obj = ConfigWithExclude(toml=t)
    assert obj.computed == "from_default"
