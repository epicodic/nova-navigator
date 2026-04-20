"""Remote connection editor dialog."""

from __future__ import annotations

import copy
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListItem, ListView, Static

from nova_navigator.config.remotes import ProxySettings, RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.decision import Decision
from nova_navigator.dialogs.dialog import Dialog
from nova_navigator.dialogs.icon_picker_dialog import IconPickerDialog
from nova_navigator.icons import ICONS
from nova_widgets import Button, Checkbox, Input, Select

_PROTOCOL_OPTIONS: list[tuple[str, str]] = [("SSH", "ssh")]


class EditRemotesDialog(Dialog):
    """Full-screen modal for editing saved remote connections."""

    DEFAULT_CSS = """
    EditRemotesDialog {
        #dialog_box {
            width: 85%;
            height: 90%;
        }

        #list_row {
            height: 1fr;
        }

        #remote_list {
            width: 1fr;
            border: inner $surface;
        }

        #action_col {
            width: auto;
            height: 1fr;
        }

        #form_container {
            height: auto;
            margin-top: 1;
            padding: 0 1;
        }

        .form_row {
            height: auto;
        }

        .form_label {
            width: auto;
            border: inner transparent;
        }

        #input_name {
            width: 1fr;
        }

        #input_icon {
            width: 20;
        }

        #btn_pick_icon {
            width: 5;
            max-width: 5;
            margin: 0 0 0 1;
        }

        #uri_preview {
            width: 1fr;
            color: $text-muted;
            border: inner transparent;
            padding: 0 1;
        }

        #select_type {
            width: 20;
        }

        #ssh_section {
            height: auto;
        }

        #input_address {
            width: 1fr;
        }

        #input_port {
            width: 10;
        }

        #input_username {
            width: 1fr;
        }

        #input_identity_file {
            width: 1fr;
        }

        #input_proxy_host {
            width: 1fr;
        }

        #input_proxy_port {
            width: 10;
        }
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("delete", "remove_item", "Remove", show=False),
        Binding("f8", "remove_item", "Remove", show=False),
    ]

    _config: RemoteConfig
    _working: list[RemoteConnection]
    _current_index: int | None
    _syncing: bool

    def __init__(self, config: RemoteConfig) -> None:
        super().__init__("Edit Remote Connections", buttons=[Decision.OK, Decision.CANCEL])
        self._config = config
        self._working = copy.deepcopy(config._items)
        self._current_index = None
        self._syncing = False

    # ------------------------------------------------------------------ compose

    def compose_content(self) -> ComposeResult:
        yield Horizontal(
            ListView(id="remote_list"),
            Vertical(
                Button("Add", id="btn_add"),
                Button("Remove", id="btn_remove", disabled=True),
                id="action_col",
            ),
            id="list_row",
        )
        yield Vertical(
            Horizontal(
                Label("Name: ", classes="form_label"),
                Input(placeholder="Name", id="input_name", disabled=True),
                Label("  Icon: ", classes="form_label"),
                Input(placeholder="Icon", id="input_icon", disabled=True),
                Button("…", id="btn_pick_icon", disabled=True),
                classes="form_row",
            ),
            Horizontal(
                Label("URI: ", classes="form_label"),
                Static("", id="uri_preview"),
                classes="form_row",
            ),
            Horizontal(
                Label("Type: ", classes="form_label"),
                Select(
                    options=[(label, value) for label, value in _PROTOCOL_OPTIONS],
                    id="select_type",
                    disabled=True,
                ),
                classes="form_row",
            ),
            Vertical(
                Static("── SSH ──", classes="form_label"),
                Horizontal(
                    Label("Address: ", classes="form_label"),
                    Input(placeholder="hostname or IP", id="input_address", disabled=True),
                    Label("  Port: ", classes="form_label"),
                    Input(placeholder="22", id="input_port", disabled=True),
                    classes="form_row",
                ),
                Horizontal(
                    Label("Username: ", classes="form_label"),
                    Input(placeholder="user", id="input_username", disabled=True),
                    classes="form_row",
                ),
                Horizontal(
                    Label("Identity File: ", classes="form_label"),
                    Input(placeholder="~/.ssh/id_ed25519", id="input_identity_file", disabled=True),
                    classes="form_row",
                ),
                id="ssh_section",
            ),
            Horizontal(
                Checkbox("Enable Proxy", id="check_proxy", disabled=True),
                Label("  Host: ", classes="form_label"),
                Input(placeholder="proxy host", id="input_proxy_host", disabled=True),
                Label("  Port: ", classes="form_label"),
                Input(placeholder="1080", id="input_proxy_port", disabled=True),
                classes="form_row",
            ),
            id="form_container",
        )

    def on_mount(self) -> None:
        self._rebuild_list(select_index=0 if self._working else None)

    # ------------------------------------------------------------------ list

    def _make_list_label(self, entry: RemoteConnection) -> str:
        icon = ICONS.get_icon(entry.icon).glyph + " " if entry.icon else ""
        uri = entry.uri or ""
        return f"{icon}{entry.name}  {uri}"

    def _rebuild_list(self, select_index: int | None) -> None:
        lv = self.query_one("#remote_list", ListView)
        lv.clear()
        for entry in self._working:
            lv.append(ListItem(Label(self._make_list_label(entry))))
        if select_index is not None and self._working:
            idx = min(select_index, len(self._working) - 1)
            lv.index = idx

    def _update_list_item_label(self, index: int) -> None:
        lv = self.query_one("#remote_list", ListView)
        items = list(lv.query(ListItem))
        if index < len(items):
            label = items[index].query_one(Label)
            label.update(self._make_list_label(self._working[index]))

    # ------------------------------------------------------------------ form sync

    def _set_form_disabled(self, disabled: bool) -> None:
        for widget_id in (
            "#input_name",
            "#input_icon",
            "#btn_pick_icon",
            "#select_type",
            "#input_address",
            "#input_port",
            "#input_username",
            "#input_identity_file",
            "#check_proxy",
            "#input_proxy_host",
            "#input_proxy_port",
        ):
            self.query_one(widget_id).disabled = disabled  # type: ignore[union-attr]

    def _sync_form(self, index: int | None) -> None:
        self._current_index = index
        self._syncing = True
        try:
            if index is None:
                self._set_form_disabled(True)
                self.query_one("#input_name", Input).value = ""
                self.query_one("#input_icon", Input).value = ""
                self.query_one("#uri_preview", Static).update("")
                self.query_one("#input_address", Input).value = ""
                self.query_one("#input_port", Input).value = ""
                self.query_one("#input_username", Input).value = ""
                self.query_one("#input_identity_file", Input).value = ""
                self.query_one("#check_proxy", Checkbox).value = False
                self.query_one("#input_proxy_host", Input).value = ""
                self.query_one("#input_proxy_port", Input).value = ""
                self.query_one("#ssh_section").display = False
                self._update_remove_button()
                return

            entry = self._working[index]
            self._set_form_disabled(False)

            self.query_one("#input_name", Input).value = entry.name
            self.query_one("#input_icon", Input).value = entry.icon or ""
            self.query_one("#uri_preview", Static).update(entry.uri or "")

            # protocol
            proto = "ssh"  # only supported for now
            self.query_one("#select_type", Select).value = proto
            self.query_one("#ssh_section").display = proto == "ssh"

            # SSH fields
            ssh = entry.ssh or SshSettings()
            self.query_one("#input_address", Input).value = ssh.host
            self.query_one("#input_port", Input).value = str(ssh.port) if ssh.port is not None else ""
            self.query_one("#input_username", Input).value = ssh.user or ""
            self.query_one("#input_identity_file", Input).value = ssh.identity_file or ""

            # proxy
            proxy_enabled = entry.proxy is not None
            self.query_one("#check_proxy", Checkbox).value = proxy_enabled
            proxy = entry.proxy or ProxySettings()
            self.query_one("#input_proxy_host", Input).value = proxy.host
            self.query_one("#input_proxy_port", Input).value = str(proxy.port) if proxy.port != 1080 else ""  # noqa: PLR2004
            self.query_one("#input_proxy_host", Input).disabled = not proxy_enabled
            self.query_one("#input_proxy_port", Input).disabled = not proxy_enabled

            self._update_remove_button()
        finally:
            self._syncing = False

    def _update_remove_button(self) -> None:
        self.query_one("#btn_remove", Button).disabled = self._current_index is None

    # ------------------------------------------------------------------ URI assembly

    def _build_uri_preview(self) -> str:
        proto = "ssh"
        address = self.query_one("#input_address", Input).value.strip()
        port_str = self.query_one("#input_port", Input).value.strip()
        username = self.query_one("#input_username", Input).value.strip()
        if not address:
            return ""
        netloc = f"{username}@{address}" if username else address
        if port_str and port_str != "22":
            netloc = f"{netloc}:{port_str}"
        return f"{proto}://{netloc}"

    def _assemble_and_store_uri(self) -> None:
        if self._current_index is None:
            return
        uri = self._build_uri_preview()
        self._working[self._current_index].uri = uri
        self.query_one("#uri_preview", Static).update(uri)
        self._update_list_item_label(self._current_index)

    # ------------------------------------------------------------------ event handlers

    @on(ListView.Highlighted)
    def _on_list_highlighted(self, event: ListView.Highlighted) -> None:
        index = event.list_view.index
        self._sync_form(index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.prevent_default()
        match event.button.id:
            case "OK":
                self.action_accept_dialog()
            case "CANCEL":
                self.dismiss(Decision.CANCEL.name)
            case "btn_add":
                self._on_add()
            case "btn_remove":
                self._on_remove()
            case "btn_pick_icon":
                self._run_pick_icon()

    def _on_add(self) -> None:
        new_entry = RemoteConnection(name="new-connection", ssh=SshSettings())
        self._working.append(new_entry)
        lv = self.query_one("#remote_list", ListView)
        lv.append(ListItem(Label(self._make_list_label(new_entry))))
        lv.index = len(self._working) - 1

    def _on_remove(self) -> None:
        if self._current_index is None:
            return
        idx = self._current_index
        self._working.pop(idx)
        new_index = min(idx, len(self._working) - 1) if self._working else None
        self._rebuild_list(select_index=new_index)
        if new_index is None:
            self._sync_form(None)

    def _run_pick_icon(self) -> None:
        current_icon = self.query_one("#input_icon", Input).value or None
        self.app.push_screen(IconPickerDialog(initial_icon=current_icon), callback=self._on_icon_picked)

    def _on_icon_picked(self, result: str | None) -> None:
        if result is None or result == Decision.CANCEL.name:
            return
        self.query_one("#input_icon", Input).value = result

    @on(Input.Changed, "#input_name")
    def _on_name_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        self._working[self._current_index].name = event.value
        self._update_list_item_label(self._current_index)

    @on(Input.Changed, "#input_icon")
    def _on_icon_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        self._working[self._current_index].icon = event.value or None
        self._update_list_item_label(self._current_index)

    @on(Input.Changed, "#input_address")
    def _on_address_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        entry.ssh.host = event.value
        self._assemble_and_store_uri()

    @on(Input.Changed, "#input_port")
    def _on_port_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        port_str = event.value.strip()
        entry.ssh.port = int(port_str) if port_str.isdigit() else None
        self._assemble_and_store_uri()

    @on(Input.Changed, "#input_username")
    def _on_username_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        entry.ssh.user = event.value or None
        self._assemble_and_store_uri()

    @on(Input.Changed, "#input_identity_file")
    def _on_identity_file_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        entry.ssh.identity_file = event.value or None

    @on(Checkbox.Changed, "#check_proxy")
    def _on_proxy_toggled(self, event: Checkbox.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        proxy_enabled = event.value
        if proxy_enabled and entry.proxy is None:
            entry.proxy = ProxySettings()
        elif not proxy_enabled:
            entry.proxy = None
        for widget_id in ("#input_proxy_host", "#input_proxy_port"):
            self.query_one(widget_id).disabled = not proxy_enabled  # type: ignore[union-attr]
        if proxy_enabled and entry.proxy:
            self._syncing = True
            try:
                self.query_one("#input_proxy_host", Input).value = entry.proxy.host
                self.query_one("#input_proxy_port", Input).value = (
                    str(entry.proxy.port) if entry.proxy.port != 1080 else ""  # noqa: PLR2004
                )
            finally:
                self._syncing = False

    @on(Input.Changed, "#input_proxy_host")
    def _on_proxy_host_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.proxy is None:
            return
        entry.proxy.host = event.value

    @on(Input.Changed, "#input_proxy_port")
    def _on_proxy_port_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.proxy is None:
            return
        port_str = event.value.strip()
        entry.proxy.port = int(port_str) if port_str.isdigit() else 1080

    # ------------------------------------------------------------------ dialog result

    def action_accept_dialog(self) -> None:
        self._config._items = self._working
        self._config.save()
        self.dismiss(Decision.OK.name)

    def action_remove_item(self) -> None:
        self._on_remove()
