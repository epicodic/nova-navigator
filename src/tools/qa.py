#!/usr/bin/env python3
import argparse
import subprocess
import sys

from rich.console import Console
from rich.rule import Rule

console = Console()


def header(msg: str, style: str) -> None:
    console.print()
    console.rule(f"[bold white] {msg} [/]", style=style)


def run_step(label: str, style: str, cmd: list[str]) -> bool:
    header(label, style)
    result = subprocess.run(cmd)
    if result.returncode == 0:
        console.print(f"  [bold green]\u2713 {label} passed[/]")
        return True
    else:
        console.print(f"  [bold red]\u2717 {label} failed[/]")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QA checks (lint, format, typecheck, tests).")
    parser.add_argument("--fix", action="store_true", help="Apply autofixes where possible.")
    args = parser.parse_args()

    steps = [
        ("Ruff Lint", "blue", ["uv", "run", "ruff", "check", "."] + (["--fix"] if args.fix else [])),
        ("Ruff Format", "yellow", ["uv", "run", "ruff", "format", "."] + ([] if args.fix else ["--check"])),
        ("Type Check", "green", ["uv", "run", "ty", "check", "."]),
        ("Tests", "magenta", ["uv", "run", "pytest", "tests/"]),
    ]

    failed = [label for label, style, cmd in steps if not run_step(label, style, cmd)]

    console.print()
    if not failed:
        console.print("[bold green]All checks passed.[/]")
    else:
        console.print("[bold red]One or more checks failed.[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
