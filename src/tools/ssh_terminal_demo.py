"""SSH terminal demo — connects to a remote SSH host and opens an interactive terminal.

Usage:
    ssh_terminal_demo <hostname> [-u USER] [-p PORT] [--shell SHELL] [--ask-password]
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import ClassVar

import paramiko
from textual.app import App, ComposeResult

from nova_navigator.terminal import SshPtyBackend, Terminal


class SshTerminalApp(App[None]):
    """Minimal full-screen terminal app backed by SshPtyBackend."""

    TITLE = "Nova Navigator — SSH Terminal Demo"

    BINDINGS: ClassVar = [
        ("ctrl+q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    Screen { padding: 0; }
    Terminal { width: 100%; height: 100%; }
    """

    def __init__(self, ssh_client: paramiko.SSHClient, shell: str) -> None:
        super().__init__()
        self._ssh_client = ssh_client
        self._shell = shell
        self._terminal: Terminal | None = None

    def compose(self) -> ComposeResult:
        backend = SshPtyBackend(ssh_client=self._ssh_client)
        self._terminal = Terminal(command=self._shell, backend=backend, keep_alive=False)
        yield self._terminal

    def on_mount(self) -> None:
        assert self._terminal is not None
        self._terminal.start()
        self._terminal.focus()

    def on_terminal_closed(self, message: Terminal.Closed) -> None:
        self.exit()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ssh_terminal_demo",
        description="Manual SSH terminal for testing SshPtyBackend against a real host.",
    )
    parser.add_argument("hostname", help="Remote SSH hostname or IP address")
    parser.add_argument("-u", "--username", default=None, help="SSH username (default: current user)")
    parser.add_argument("-p", "--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument(
        "--shell",
        default="zsh",
        metavar="SHELL",
        help="Remote shell hint for driver detection: zsh, bash, or sh (default: zsh)",
    )
    parser.add_argument(
        "--ask-password",
        action="store_true",
        help="Prompt for SSH password instead of using key-based auth",
    )
    parser.add_argument(
        "--accept-new-key",
        action="store_true",
        help="Accept and save unknown host keys to ~/.ssh/known_hosts",
    )
    args = parser.parse_args()

    username: str = args.username or getpass.getuser()
    password: str | None = None
    if args.ask_password:
        password = getpass.getpass(f"SSH password for {username}@{args.hostname}: ")

    print(f"Connecting to {username}@{args.hostname}:{args.port} …")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if args.accept_new_key:
        known_hosts = os.path.expanduser("~/.ssh/known_hosts")
        if os.path.exists(known_hosts):
            client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(args.hostname, port=args.port, username=username, password=password)
        if args.accept_new_key:
            client.save_host_keys(os.path.expanduser("~/.ssh/known_hosts"))
    except (OSError, paramiko.SSHException) as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        if "not found in known_hosts" in str(exc).lower() or "unknown host key" in str(exc).lower():
            print("Tip: use --accept-new-key to add the host key on first connect.", file=sys.stderr)
        sys.exit(1)

    print("Connected. Starting terminal… (Ctrl+Q to quit)")
    app = SshTerminalApp(ssh_client=client, shell=args.shell)
    try:
        app.run()
    finally:
        client.close()
