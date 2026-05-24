from nova_widgets.keymap.format import KeyDisplayStyle, format_key

# --- CLASSIC style ---


def test_format_ctrl_v_classic() -> None:
    assert format_key("ctrl+v", KeyDisplayStyle.CLASSIC) == "Ctrl+V"


def test_format_f5_classic() -> None:
    assert format_key("f5", KeyDisplayStyle.CLASSIC) == "F5"


def test_format_alt_x_classic() -> None:
    assert format_key("alt+x", KeyDisplayStyle.CLASSIC) == "Alt+X"


def test_format_ctrl_shift_g_classic() -> None:
    assert format_key("ctrl+shift+g", KeyDisplayStyle.CLASSIC) == "Ctrl+Shift+G"


def test_format_enter_classic() -> None:
    assert format_key("enter", KeyDisplayStyle.CLASSIC) == "Enter"


def test_format_multi_chord_classic() -> None:
    assert format_key("ctrl+x ctrl+s", KeyDisplayStyle.CLASSIC) == "Ctrl+X Ctrl+S"


# --- EMACS style ---


def test_format_ctrl_v_emacs() -> None:
    assert format_key("ctrl+v", KeyDisplayStyle.EMACS) == "C-v"


def test_format_alt_x_emacs() -> None:
    assert format_key("alt+x", KeyDisplayStyle.EMACS) == "M-x"


def test_format_f5_emacs() -> None:
    assert format_key("f5", KeyDisplayStyle.EMACS) == "f5"


def test_format_multi_chord_emacs() -> None:
    assert format_key("ctrl+x ctrl+s", KeyDisplayStyle.EMACS) == "C-x C-s"


# --- CARET style ---


def test_format_ctrl_v_caret() -> None:
    assert format_key("ctrl+v", KeyDisplayStyle.CARET) == "^V"


def test_format_f5_caret() -> None:
    assert format_key("f5", KeyDisplayStyle.CARET) == "F5"


def test_format_alt_x_caret() -> None:
    assert format_key("alt+x", KeyDisplayStyle.CARET) == "Alt+X"


def test_format_ctrl_b_caret() -> None:
    assert format_key("ctrl+b", KeyDisplayStyle.CARET) == "^B"


def test_format_multi_chord_caret() -> None:
    assert format_key("ctrl+x ctrl+s", KeyDisplayStyle.CARET) == "^X ^S"
