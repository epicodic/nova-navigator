from nova_widgets.unicode import center, ljust, rjust


def test_ljust_pads_short_string() -> None:
    assert ljust("ab", 5) == "ab   "


def test_ljust_equal_width_unchanged() -> None:
    assert ljust("abcde", 5) == "abcde"


def test_ljust_longer_than_width_unchanged() -> None:
    assert ljust("abcdef", 5) == "abcdef"


def test_ljust_custom_fillchar() -> None:
    assert ljust("a", 4, "-") == "a---"


def test_ljust_wide_char_counts_as_two_columns() -> None:
    # "中" is 2 terminal columns; width=4 → 2 spaces added
    assert ljust("中", 4) == "中  "


def test_ljust_wide_char_exact_width() -> None:
    # "中" is 2 cols; width=2 → no padding
    assert ljust("中", 2) == "中"


def test_rjust_pads_short_string() -> None:
    assert rjust("ab", 5) == "   ab"


def test_rjust_equal_width_unchanged() -> None:
    assert rjust("abcde", 5) == "abcde"


def test_rjust_custom_fillchar() -> None:
    assert rjust("a", 4, "-") == "---a"


def test_rjust_wide_char_counts_as_two_columns() -> None:
    # "中" is 2 cols; width=4 → 2 spaces prepended
    assert rjust("中", 4) == "  中"


def test_center_pads_both_sides_evenly() -> None:
    # "a" is 1 col; width=5 → total padding 4; left=2, right=2
    assert center("a", 5) == "  a  "


def test_center_odd_total_padding_extra_on_right() -> None:
    # "ab" is 2 cols; width=5 → total padding 3; left=1, right=2
    assert center("ab", 5) == " ab  "


def test_center_custom_fillchar() -> None:
    assert center("x", 5, "-") == "--x--"


def test_center_wide_char() -> None:
    # "中" is 2 cols; width=6 → total padding 4; left=2, right=2
    assert center("中", 6) == "  中  "
