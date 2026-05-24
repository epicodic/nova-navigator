from nova_widgets.keymap.chord import ChordStateMachine


def _make_machine(bindings: dict[str, str]) -> ChordStateMachine:
    """Build a ChordStateMachine from {action_name: key_sequence}."""
    m = ChordStateMachine()
    m.build_trie(bindings)
    return m


# --- single-key bindings ---


def test_single_key_match_returns_action() -> None:
    m = _make_machine({"browser.copy": "f5"})
    result = m.feed("f5")
    assert result.consumed is True
    assert result.action_name == "browser.copy"


def test_single_key_no_match_returns_not_consumed() -> None:
    m = _make_machine({"browser.copy": "f5"})
    result = m.feed("f6")
    assert result.consumed is False
    assert result.action_name is None


# --- multi-chord sequences ---


def test_chord_prefix_enters_pending_state() -> None:
    m = _make_machine({"app.settings": "ctrl+x ctrl+s"})
    result = m.feed("ctrl+x")
    assert result.consumed is True
    assert result.action_name is None
    assert result.continuations is not None


def test_chord_prefix_shows_continuation() -> None:
    m = _make_machine({"app.settings": "ctrl+x ctrl+s"})
    result = m.feed("ctrl+x")
    assert result.continuations is not None
    keys = [k for k, _ in result.continuations]
    assert "ctrl+s" in keys


def test_chord_second_key_dispatches() -> None:
    m = _make_machine({"app.settings": "ctrl+x ctrl+s"})
    m.feed("ctrl+x")
    result = m.feed("ctrl+s")
    assert result.consumed is True
    assert result.action_name == "app.settings"


def test_chord_second_key_no_match_falls_through() -> None:
    m = _make_machine({"app.settings": "ctrl+x ctrl+s"})
    m.feed("ctrl+x")
    result = m.feed("ctrl+z")
    assert result.consumed is False


def test_escape_resets_state() -> None:
    m = _make_machine({"app.settings": "ctrl+x ctrl+s"})
    m.feed("ctrl+x")
    result = m.feed("escape")
    # escape itself is not consumed
    assert result.consumed is False
    # after escape, ctrl+x should restart chord
    result2 = m.feed("ctrl+x")
    assert result2.consumed is True


def test_machine_idle_after_dispatch() -> None:
    m = _make_machine({"browser.copy": "f5"})
    m.feed("f5")
    # should be back in IDLE; f5 again should dispatch again
    result = m.feed("f5")
    assert result.consumed is True
    assert result.action_name == "browser.copy"


def test_multiple_bindings_in_trie() -> None:
    m = _make_machine(
        {
            "browser.copy": "f5",
            "browser.move": "f6",
            "app.quit": "ctrl+q",
        }
    )
    assert m.feed("f5").action_name == "browser.copy"
    assert m.feed("f6").action_name == "browser.move"
    assert m.feed("ctrl+q").action_name == "app.quit"
