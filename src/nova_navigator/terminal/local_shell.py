"""Choice of the shell that the local embedded terminal starts."""

from __future__ import annotations

import os
import pwd
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

_LAST_RESORT = "/bin/sh"


@dataclass(frozen=True)
class ShellChoice:
    """Shell to start.

    Attributes:
        path: Executable to run.
        rejected: Value of ``$SHELL`` that could not be executed, or None.
    """

    path: str
    rejected: str | None = None


def _login_shell() -> str:
    """Return the login shell of the current account from the password database."""
    return pwd.getpwuid(os.getuid()).pw_shell


def resolve_local_shell(env: Mapping[str, str], login_shell: Callable[[], str] = _login_shell) -> ShellChoice:
    """Return the shell to start: ``$SHELL`` when it can be executed, else the login shell, else ``/bin/sh``.

    An executable ``$SHELL`` is always used, whatever it is.  A set but unusable value is
    reported through ``rejected``; an unset or empty value is not.

    Args:
        env: Environment to read ``SHELL`` from.
        login_shell: Returns the login shell of the account, or raises ``KeyError`` when there is none.

    Returns:
        The chosen shell and the rejected ``$SHELL`` value, if any.
    """
    wanted = env.get("SHELL", "")
    found = shutil.which(wanted) if wanted else None
    if found:
        return ShellChoice(found)
    try:
        login = login_shell()
    except KeyError:
        login = ""
    fallback = shutil.which(login) if login else None
    return ShellChoice(fallback or _LAST_RESORT, wanted or None)
