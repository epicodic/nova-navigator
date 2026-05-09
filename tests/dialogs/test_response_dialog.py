"""Tests for ResponseDialog, OverwriteResponseDialog, and make_response_dialog."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from textual import widgets
from textual.app import App, ComposeResult

from nova_navigator.dialogs.response_dialog import (
    OverwriteResponseDialog,
    ResponseDialog,
    make_response_dialog,
)
from nova_navigator.response import Response
from nova_navigator.scheduler import ResponseRequest

# ── fixtures ──────────────────────────────────────────────────────────────────

_BASIC_REQUEST = ResponseRequest(
    title="Confirm",
    expected_responses=[Response.YES, Response.NO],
    message="Are you sure?",
)

_OVERWRITE_REQUEST = ResponseRequest(
    title="Overwrite?",
    expected_responses=[Response.YES, Response.NO],
    message="File already exists.",
    dialog_type="overwrite",
    details={"src_name": "source.txt", "src_size": 1024, "dst_name": "dest.txt", "dst_size": 512},
)


def _make_app(dialog: ResponseDialog) -> App[None]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return _App()


@pytest.fixture(autouse=True)
def patch_format_size() -> Generator[None, None, None]:
    """Avoid GlobalConfig dependency inside OverwriteResponseDialog._details_content."""
    with patch("nova_navigator.dialogs.response_dialog.format_size", side_effect=lambda n: f"{n}B"):
        yield


# ── make_response_dialog factory ──────────────────────────────────────────────


def test_make_response_dialog_returns_overwrite_dialog_for_overwrite_type() -> None:
    request = ResponseRequest("t", [], "m", dialog_type="overwrite")
    dialog = make_response_dialog(request)
    assert isinstance(dialog, OverwriteResponseDialog)


def test_make_response_dialog_returns_generic_dialog_for_unknown_type() -> None:
    request = ResponseRequest("t", [], "m", dialog_type="something_else")
    dialog = make_response_dialog(request)
    assert type(dialog) is ResponseDialog


def test_make_response_dialog_returns_generic_dialog_when_no_type() -> None:
    request = ResponseRequest("t", [], "m")
    dialog = make_response_dialog(request)
    assert type(dialog) is ResponseDialog


# ── ResponseDialog._details_content ──────────────────────────────────────────


def test_response_dialog_details_content_is_empty() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)
    assert dialog._details_content() == []


# ── ResponseDialog compose: buttons ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_dialog_mounts_one_button_per_expected_response() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)
    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        buttons = list(dialog.query(widgets.Button))
        assert len(buttons) == 2


@pytest.mark.asyncio
async def test_dialog_positive_response_button_has_success_variant() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)
    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        yes_btn = dialog.query_one("#YES", widgets.Button)
        assert yes_btn.variant == "success"


@pytest.mark.asyncio
async def test_dialog_negative_response_button_has_error_variant() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)
    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        no_btn = dialog.query_one("#NO", widgets.Button)
        assert no_btn.variant == "error"


@pytest.mark.asyncio
async def test_dialog_message_label_is_present() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)
    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        label = dialog.query_one(widgets.Label)
        assert label is not None


@pytest.mark.asyncio
async def test_dialog_to_all_response_produces_extra_button_row() -> None:
    request = ResponseRequest(
        title="t",
        expected_responses=[Response.YES, Response.NO, Response.ALL],
        message="m",
    )
    dialog = ResponseDialog(request)
    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        buttons = list(dialog.query(widgets.Button))
        assert len(buttons) == 3
        assert dialog.query_one("#ALL", widgets.Button) is not None


# ── ResponseDialog.on_button_pressed ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_button_pressed_dismisses_with_corresponding_response() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)

    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        with patch.object(dialog, "dismiss", wraps=dialog.dismiss) as mock_dismiss:
            event = MagicMock()
            event.button.id = "YES"
            dialog.on_button_pressed(event)
            await pilot.pause()
        mock_dismiss.assert_called_once_with(Response.YES)


@pytest.mark.asyncio
async def test_on_button_pressed_no_dismisses_with_no() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)

    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        with patch.object(dialog, "dismiss", wraps=dialog.dismiss) as mock_dismiss:
            event = MagicMock()
            event.button.id = "NO"
            dialog.on_button_pressed(event)
            await pilot.pause()
        mock_dismiss.assert_called_once_with(Response.NO)


# ── ResponseDialog.action_abort ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_abort_dismisses_with_response_no() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)

    async with _make_app(dialog).run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        with patch.object(dialog, "dismiss", wraps=dialog.dismiss) as mock_dismiss:
            dialog.action_abort()
            await pilot.pause()
        mock_dismiss.assert_called_once_with(Response.NO)


# ── OverwriteResponseDialog._details_content ─────────────────────────────────


def test_overwrite_details_content_returns_single_static() -> None:
    dialog = OverwriteResponseDialog(_OVERWRITE_REQUEST)
    content = dialog._details_content()
    assert len(content) == 1
    assert isinstance(content[0], widgets.Static)


def test_overwrite_details_content_static_has_file_info_id() -> None:
    dialog = OverwriteResponseDialog(_OVERWRITE_REQUEST)
    content = dialog._details_content()
    assert content[0].id == "file_info"


def test_overwrite_details_content_includes_src_and_dst_names() -> None:
    """File names appear in the constructed file_info text (verified via mounted dialog)."""
    dialog = OverwriteResponseDialog(_OVERWRITE_REQUEST)
    # Verify a Static widget is returned with the expected id — content is
    # checked in test_overwrite_dialog_shows_file_info_text below.
    content = dialog._details_content()
    assert len(content) == 1
    assert content[0].id == "file_info"


def test_overwrite_details_content_uses_fallback_when_keys_absent() -> None:
    request = ResponseRequest("t", [], "m", dialog_type="overwrite", details={})
    dialog = OverwriteResponseDialog(request)
    # Must not raise; fallback "?" values are used for missing keys
    content = dialog._details_content()
    assert len(content) == 1


# ── OverwriteResponseDialog mounts ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overwrite_dialog_mounts_file_info_widget() -> None:
    dialog = OverwriteResponseDialog(_OVERWRITE_REQUEST)
    async with _make_app(dialog).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert dialog.query_one("#file_info", widgets.Static) is not None


@pytest.mark.asyncio
async def test_overwrite_dialog_shows_file_info_text() -> None:
    dialog = OverwriteResponseDialog(_OVERWRITE_REQUEST)
    async with _make_app(dialog).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        static = dialog.query_one("#file_info", widgets.Static)
        rendered = str(static.render())
        assert "source.txt" in rendered
        assert "dest.txt" in rendered


# ── ResponseDialog.run ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_pushes_screen_and_returns_dismissed_value() -> None:
    dialog = ResponseDialog(_BASIC_REQUEST)
    result_holder: list[Response] = []

    class _App(App[None]):
        def on_mount(self) -> None:
            # push_screen_wait requires a worker context
            self.run_worker(self._run_dialog())

        async def _run_dialog(self) -> None:
            result_holder.append(await dialog.run())

    async with _App().run_test(size=(80, 20)) as pilot:
        await pilot.pause(delay=0.05)
        dialog.action_abort()
        await pilot.pause(delay=0.1)
        assert result_holder == [Response.NO]
