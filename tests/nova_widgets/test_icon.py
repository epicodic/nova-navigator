import io

from nova_navigator.icons import IconSet
from nova_widgets.icon import Icon

# --- Icon.of ---


def test_of_none_produces_blank_glyph() -> None:
    icon = Icon.of(None)
    assert icon.glyph == "  "


def test_of_none_is_not_animated() -> None:
    assert not Icon.of(None).is_animated


def test_of_ascii_glyph_padded_to_width_2() -> None:
    icon = Icon.of("x")
    assert icon.glyph == "x "


def test_of_wide_glyph_no_extra_padding() -> None:
    icon = Icon.of("⭐")
    assert icon.glyph == "⭐"


def test_of_single_frame_is_not_animated() -> None:
    assert not Icon.of("●").is_animated


def test_of_color_stored() -> None:
    icon = Icon.of("●", color=(255, 0, 0))
    assert icon.color == (255, 0, 0)


def test_of_markup_plain_when_no_color() -> None:
    icon = Icon.of("●")
    assert icon.markup == "● "


def test_of_markup_with_color() -> None:
    icon = Icon.of("●", color=(255, 0, 0))
    assert icon.markup == "[rgb(255,0,0)]● [/]"


# --- Icon.from_glyphs ---


def test_from_glyphs_multi_frame_is_animated() -> None:
    icon = Icon.from_glyphs(["○", "◔", "◑", "◕", "●"])
    assert icon.is_animated


def test_from_glyphs_single_frame_is_not_animated() -> None:
    icon = Icon.from_glyphs(["●"])
    assert not icon.is_animated


def test_from_glyphs_empty_produces_blank() -> None:
    icon = Icon.from_glyphs([])
    assert icon.glyph == "  "


def test_from_glyphs_frames_count() -> None:
    icon = Icon.from_glyphs(["○", "◔", "◑", "◕", "●"])
    assert len(icon.frames) == 5


def test_from_glyphs_frames_are_single_frame_icons() -> None:
    icon = Icon.from_glyphs(["○", "●"])
    for frame in icon.frames:
        assert not frame.is_animated


def test_from_glyphs_frames_carry_color() -> None:
    icon = Icon.from_glyphs(["○", "●"], color=(1, 2, 3))
    for frame in icon.frames:
        assert frame.color == (1, 2, 3)


# --- Icon() blank constructor ---


def test_blank_constructor_produces_blank() -> None:
    icon = Icon()
    assert icon.glyph == "  "


def test_blank_constructor_not_animated() -> None:
    assert not Icon().is_animated


# --- frames property on single-frame icon ---


def test_single_frame_icon_frames_returns_self_list() -> None:
    icon = Icon.of("●")
    assert icon.frames == [icon]


# --- not a str subclass ---


def test_icon_is_not_str() -> None:
    assert not isinstance(Icon.of("x"), str)


# --- CSV multi-frame loading ---


def test_spinner_from_csv_is_animated() -> None:
    csv = "spinner,U+ee06U+ee07U+ee08U+ee09U+ee0a,○◔◑◕●\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.UNICODE)
    icon = iconset.get_icon("spinner")
    assert icon.is_animated
    assert len(icon.frames) == 5


def test_spinner_nerdfont_frames_from_csv() -> None:
    csv = "spinner,U+ee06U+ee07U+ee08U+ee09U+ee0a,○◔◑◕●\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.NERDFONT)
    icon = iconset.get_icon("spinner")
    assert icon.is_animated
    assert len(icon.frames) == 5


def test_single_glyph_csv_row_is_not_animated() -> None:
    csv = "file,U+f15b,📄\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.UNICODE)
    icon = iconset.get_icon("file")
    assert not icon.is_animated


def test_variation_selector_glyph_is_one_frame() -> None:
    # ✏️ is U+270F + U+FE0F — should be treated as a single grapheme cluster
    csv = "edit,U+f044,✏️\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.UNICODE)
    icon = iconset.get_icon("edit")
    assert not icon.is_animated
