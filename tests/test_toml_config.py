import tomlkit

from nova_navigator.toml_config import Field, TomlConfig, TOMLTable


class NestedConfig(TomlConfig):
    field1: str = Field(default="nested_default")
    field2: int = Field(default=0)


class BasicClass(TomlConfig):
    field1: str = Field(default="default_value")
    field2: int
    field3: list[int] = Field(default=[])
    nested: NestedConfig = Field()


class ClassWithArray(TomlConfig):
    array: list[NestedConfig] = Field(default=[])


def test_toml_config() -> None:
    t = tomlkit.loads("""
    [section]
    field1 = "custom_value"
    field2 = 42
    field3 = [1, 2, 3]

    [section.nested]
    field1 = "value"
    field2 = 100

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

    # v = t["section"]

    # print(t)
    section = t["section"]
    assert isinstance(section, TOMLTable)

    obj = BasicClass(section)
    assert isinstance(obj, BasicClass)
    assert isinstance(obj.field1, str)
    assert obj.field1 == "custom_value"
    assert isinstance(obj.field2, int)
    assert obj.field2 == 42
    assert isinstance(obj.field3, list)
    assert obj.field3 == [1, 2, 3]
    assert isinstance(obj.field3[0], int)
    assert obj.field3[0] == 1

    assert isinstance(obj.nested, NestedConfig)
    assert isinstance(obj.nested.field1, str)
    assert isinstance(obj.nested.field2, int)

    arr_obj = ClassWithArray(toml=t)
    assert isinstance(arr_obj.array, list)
    for i, item in enumerate(arr_obj.array):
        assert isinstance(item, NestedConfig)
        assert isinstance(item.field1, str)
        assert item.field1 == f"item{i + 1}"
        assert isinstance(item.field2, int)
        assert item.field2 == (i + 1) * 10


def test_optional_field() -> None:
    class ConfigWithOptionalField(TomlConfig):
        field1: str
        field2: str | None

    t = tomlkit.loads("""
    field1 = "value1"
    """)

    obj = ConfigWithOptionalField(toml=t)
    assert isinstance(obj, ConfigWithOptionalField)
    assert isinstance(obj.field1, str)
    assert obj.field1 == "value1"
    assert obj.field2 is None


def test_default_factory_field() -> None:
    class ConfigWithDefaultFactory(TomlConfig):
        field1: list[int] = Field(default_factory=lambda _: [])
        field2: dict[str, str] = Field(default_factory=lambda _: {})
        field3: str = Field(default="hello")
        field4: str | None = Field(default_factory=lambda data: data.field3.upper())

    t = tomlkit.loads("""
    """)

    obj = ConfigWithDefaultFactory(toml=t)
    assert isinstance(obj, ConfigWithDefaultFactory)
    assert isinstance(obj.field1, list)
    assert obj.field1 == []
    assert isinstance(obj.field2, dict)
    assert obj.field2 == {}
    assert isinstance(obj.field3, str)
    assert obj.field3 == "hello"
    assert isinstance(obj.field4, str)
    assert obj.field4 == "HELLO"


if __name__ == "__main__":
    test_default_factory_field()
