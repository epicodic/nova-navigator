from nova_widgets import Icon


def test_icon_none_produces_two_spaces() -> None:
    icon = Icon(None)
    assert str(icon) == "  "


def test_icon_none_len_is_2() -> None:
    icon = Icon(None)
    assert len(icon) == 2


def test_icon_ascii_char_is_padded_to_width_2() -> None:
    icon = Icon("x")
    assert str(icon).startswith("x")
    assert len(str(icon)) == 2


def test_icon_ascii_char_len_is_2() -> None:
    icon = Icon("x")
    assert len(icon) == 2


def test_icon_wide_glyph_no_extra_padding() -> None:
    # CJK character "中" occupies 2 terminal columns — no padding should be added
    icon = Icon("中")
    assert len(icon) == 2
    assert "中" in str(icon)


def test_icon_is_str_subclass() -> None:
    icon = Icon("x")
    assert isinstance(icon, str)


def test_icon_can_be_used_as_string() -> None:
    icon = Icon("x")
    assert icon + "y" == str(icon) + "y"
