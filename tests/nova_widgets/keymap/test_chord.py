from nova_widgets.keymap.chord import ChordStateMachine


def _make_machine(bindings: dict[str, tuple[str, list[str]]]) -> ChordStateMachine:
    """Build a ChordStateMachine from {action_name: (key_sequence, contexts)}."""
    m = ChordStateMachine()
    m.build_trie(bindings)
    return m


# --- single-key bindings ---


def test_single_key_match_returns_action() -> None:
    m = _make_machine({"browser.copy": ("f5", ["browser", "browser.selection"])})
    result = m.feed("f5", "browser")
    assert result.consumed is True
    assert result.action_name == "browser.copy"


def test_single_key_no_match_returns_not_consumed() -> None:
    m = _make_machine({"browser.copy": ("f5", ["browser"])})
    result = m.feed("f6", "browser")
    assert result.consumed is False
    assert result.action_name is None


def test_single_key_wrong_context_not_consumed() -> None:
    m = _make_machine({"browser.copy": ("f5", ["browser"])})
    result = m.feed("f5", "terminal")
    assert result.consumed is False


def test_single_key_correct_among_multiple_contexts() -> None:
    m = _make_machine({"browser.copy": ("f5", ["browser", "browser.selection"])})
    result = m.feed("f5", "browser.selection")
    assert result.consumed is True
    assert result.action_name == "browser.copy"


# --- multi-chord sequences ---


def test_chord_prefix_enters_pending_state() -> None:
    m = _make_machine({"app.settings": ("ctrl+x ctrl+s", ["browser", "dialog"])})
    result = m.feed("ctrl+x", "browser")
    assert result.consumed is True
    assert result.action_name is None
    assert result.continuations is not None


def test_chord_prefix_shows_continuation() -> None:
    m = _make_machine({"app.settings": ("ctrl+x ctrl+s", ["browser"])})
    result = m.feed("ctrl+x", "browser")
    assert result.continuations is not None
    keys = [k for k, _ in result.continuations]
    assert "ctrl+s" in keys


def test_chord_second_key_dispatches() -> None:
    m = _make_machine({"app.settings": ("ctrl+x ctrl+s", ["browser"])})
    m.feed("ctrl+x", "browser")
    result = m.feed("ctrl+s", "browser")
    assert result.consumed is True
    assert result.action_name == "app.settings"


def test_chord_prefix_wrong_context_falls_through() -> None:
    m = _make_machine({"app.settings": ("ctrl+x ctrl+s", ["browser"])})
    result = m.feed("ctrl+x", "terminal")
    assert result.consumed is False


def test_chord_second_key_no_match_falls_through() -> None:
    m = _make_machine({"app.settings": ("ctrl+x ctrl+s", ["browser"])})
    m.feed("ctrl+x", "browser")
    result = m.feed("ctrl+z", "browser")
    assert result.consumed is False


def test_escape_resets_state() -> None:
    m = _make_machine({"app.settings": ("ctrl+x ctrl+s", ["browser"])})
    m.feed("ctrl+x", "browser")
    result = m.feed("escape", "browser")
    # escape itself is not consumed
    assert result.consumed is False
    # after escape, ctrl+x should restart chord
    result2 = m.feed("ctrl+x", "browser")
    assert result2.consumed is True


def test_machine_idle_after_dispatch() -> None:
    m = _make_machine({"browser.copy": ("f5", ["browser"])})
    m.feed("f5", "browser")
    # should be back in IDLE; f5 again should dispatch again
    result = m.feed("f5", "browser")
    assert result.consumed is True
    assert result.action_name == "browser.copy"


def test_multiple_bindings_in_trie() -> None:
    m = _make_machine(
        {
            "browser.copy": ("f5", ["browser"]),
            "browser.move": ("f6", ["browser"]),
            "app.quit": ("ctrl+q", ["browser", "terminal"]),
        }
    )
    assert m.feed("f5", "browser").action_name == "browser.copy"
    assert m.feed("f6", "browser").action_name == "browser.move"
    assert m.feed("ctrl+q", "terminal").action_name == "app.quit"


# --- hierarchical / sub-context matching ---


def test_parent_context_matches_sub_context() -> None:
    """contexts=["browser"] should fire in "browser.selection"."""
    m = _make_machine({"browser.copy": ("f5", ["browser"])})
    result = m.feed("f5", "browser.selection")
    assert result.consumed is True
    assert result.action_name == "browser.copy"


def test_parent_context_prefix_node_matches_sub_context() -> None:
    """Multi-chord prefix registered under "browser" is reachable from "browser.selection"."""
    m = _make_machine({"app.settings": ("ctrl+x ctrl+s", ["browser"])})
    result = m.feed("ctrl+x", "browser.selection")
    assert result.consumed is True
    assert result.continuations is not None


def test_sub_context_does_not_match_sibling_context() -> None:
    """ "browser.selection" must NOT match a binding registered under "terminal"."""
    m = _make_machine({"term.paste": ("ctrl+v", ["terminal"])})
    result = m.feed("ctrl+v", "browser.selection")
    assert result.consumed is False


def test_deeply_nested_sub_context_matches_ancestor() -> None:
    """contexts=["browser"] should fire in "browser.foo.bar"."""
    m = _make_machine({"browser.copy": ("f5", ["browser"])})
    result = m.feed("f5", "browser.foo.bar")
    assert result.consumed is True
    assert result.action_name == "browser.copy"
