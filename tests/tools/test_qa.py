"""The QA runner asks the real-server pass to report why tests were skipped."""

import sys

import pytest

from tools import qa


def test_real_server_pass_reports_skip_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    steps: dict[str, list[str]] = {}

    def _record(label: str, _style: str, cmd: list[str], _ok_codes: tuple[int, ...] = (0,)) -> bool:
        steps[label] = cmd
        return True

    monkeypatch.setattr(qa, "run_step", _record)
    monkeypatch.setattr(sys, "argv", ["qa"])
    qa.main()
    assert "-rs" in steps["Real-server tests (serial)"]
