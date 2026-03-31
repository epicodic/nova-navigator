from __future__ import annotations

from typing import Any, cast

from textual import _xterm_parser, events

from nova_navigator.runtime_patches import apply_runtime_patches


def test_apply_runtime_patches_is_idempotent_and_patches_known_alt_sequences() -> None:
    original = _xterm_parser.XTermParser._sequence_to_key_events

    try:
        apply_runtime_patches()
        patched_once = _xterm_parser.XTermParser._sequence_to_key_events

        apply_runtime_patches()
        patched_twice = _xterm_parser.XTermParser._sequence_to_key_events

        assert patched_once is patched_twice

        key_events_alt_enter = list(patched_once(cast("Any", object()), "\x1b\r"))
        assert len(key_events_alt_enter) == 1
        assert isinstance(key_events_alt_enter[0], events.Key)
        assert key_events_alt_enter[0].key == "alt+enter"

        key_events_alt_b = list(patched_once(cast("Any", object()), "\x1bb"))
        assert len(key_events_alt_b) == 1
        assert isinstance(key_events_alt_b[0], events.Key)
        assert key_events_alt_b[0].key == "alt+b"

        key_events_alt_f = list(patched_once(cast("Any", object()), "\x1bf"))
        assert len(key_events_alt_f) == 1
        assert isinstance(key_events_alt_f[0], events.Key)
        assert key_events_alt_f[0].key == "alt+f"

        key_events_alt_tab = list(patched_once(cast("Any", object()), "\x1b\t"))
        assert len(key_events_alt_tab) == 1
        assert isinstance(key_events_alt_tab[0], events.Key)
        assert key_events_alt_tab[0].key == "alt+tab"

        key_events_left = list(patched_once(cast("Any", object()), "\x1b[D"))
        assert len(key_events_left) == 1
        assert isinstance(key_events_left[0], events.Key)
        assert key_events_left[0].key == "left"
    finally:
        _xterm_parser.XTermParser._sequence_to_key_events = original
