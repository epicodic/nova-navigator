from nova_widgets.keymap.key_sequence import KeyChord, KeyFormatStyle, KeySequence

# --- CLASSIC style ---


def test_format_ctrl_v_classic() -> None:
    assert KeySequence.parse("ctrl+v").format(KeyFormatStyle.CLASSIC) == "Ctrl+V"


def test_format_f5_classic() -> None:
    assert KeySequence.parse("f5").format(KeyFormatStyle.CLASSIC) == "F5"


def test_format_alt_x_classic() -> None:
    assert KeySequence.parse("alt+x").format(KeyFormatStyle.CLASSIC) == "Alt+X"


def test_format_ctrl_shift_g_classic() -> None:
    assert KeySequence.parse("ctrl+shift+g").format(KeyFormatStyle.CLASSIC) == "Ctrl+Shift+G"


def test_format_enter_classic() -> None:
    assert KeySequence.parse("enter").format(KeyFormatStyle.CLASSIC) == "Enter"


def test_format_multi_chord_classic() -> None:
    assert KeySequence.parse("ctrl+x ctrl+s").format(KeyFormatStyle.CLASSIC) == "Ctrl+X Ctrl+S"


# --- EMACS style ---


def test_format_ctrl_v_emacs() -> None:
    assert KeySequence.parse("ctrl+v").format(KeyFormatStyle.EMACS) == "C-v"


def test_format_alt_x_emacs() -> None:
    assert KeySequence.parse("alt+x").format(KeyFormatStyle.EMACS) == "M-x"


def test_format_f5_emacs() -> None:
    assert KeySequence.parse("f5").format(KeyFormatStyle.EMACS) == "f5"


def test_format_multi_chord_emacs() -> None:
    assert KeySequence.parse("ctrl+x ctrl+s").format(KeyFormatStyle.EMACS) == "C-x C-s"


# --- CARET style ---


def test_format_ctrl_v_caret() -> None:
    assert KeySequence.parse("ctrl+v").format(KeyFormatStyle.CARET) == "^V"


def test_format_f5_caret() -> None:
    assert KeySequence.parse("f5").format(KeyFormatStyle.CARET) == "F5"


def test_format_alt_x_caret() -> None:
    assert KeySequence.parse("alt+x").format(KeyFormatStyle.CARET) == "Alt+X"


def test_format_ctrl_b_caret() -> None:
    assert KeySequence.parse("ctrl+b").format(KeyFormatStyle.CARET) == "^B"


def test_format_multi_chord_caret() -> None:
    assert KeySequence.parse("ctrl+x ctrl+s").format(KeyFormatStyle.CARET) == "^X ^S"


# --- Canonical ordering ---


def test_keychord_canonical_order() -> None:
    """x+ctrl should equal ctrl+x."""
    assert KeyChord.parse("x+ctrl") == KeyChord.parse("ctrl+x")


def test_keychord_canonical_order_multi_modifier() -> None:
    """Modifiers are sorted ctrl < alt < shift < meta."""
    assert KeyChord.parse("shift+ctrl+g") == KeyChord.parse("ctrl+shift+g")


# --- KeySequence.suffix_after ---


def test_suffix_after_single_chord_prefix() -> None:
    seq = KeySequence.parse("ctrl+x ctrl+s")
    result = seq.suffix_after(KeySequence.parse("ctrl+x"))
    assert result == KeySequence.parse("ctrl+s")


def test_suffix_after_multi_chord_prefix() -> None:
    seq = KeySequence.parse("ctrl+k ctrl+a ctrl+b")
    result = seq.suffix_after(KeySequence.parse("ctrl+k ctrl+a"))
    assert result == KeySequence.parse("ctrl+b")


def test_suffix_after_prefix_not_found_returns_self() -> None:
    seq = KeySequence.parse("ctrl+x ctrl+s")
    result = seq.suffix_after(KeySequence.parse("ctrl+k"))
    assert result is seq


def test_suffix_after_empty_prefix_returns_self() -> None:
    seq = KeySequence.parse("ctrl+x ctrl+s")
    result = seq.suffix_after(KeySequence(()))
    assert result is seq


def test_suffix_after_full_sequence_prefix_returns_empty() -> None:
    seq = KeySequence.parse("ctrl+x ctrl+s")
    result = seq.suffix_after(seq)
    assert result == KeySequence(())
