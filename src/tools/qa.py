#!/usr/bin/env python3
import argparse
import subprocess
import sys

from rich.console import Console

console = Console()


def header(msg: str, style: str) -> None:
    console.print()
    console.rule(f"[bold white] {msg} [/]", style=style)


def run_step(label: str, style: str, cmd: list[str], ok_codes: tuple[int, ...] = (0,)) -> bool:
    header(label, style)
    result = subprocess.run(cmd, check=False)
    if result.returncode in ok_codes:
        console.print(f"  [bold green]\u2713 {label} passed[/]")
        return True
    else:
        console.print(f"  [bold red]\u2717 {label} failed[/]")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QA checks (lint, format, typecheck, tests).")
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="Do not run tests, only code quality checks (format, lint, type checks).",
    )
    parser.add_argument(
        "--no-integration-tests",
        action="store_true",
        help="Do not run integration tests.",
    )
    parser.add_argument("--fix", action="store_true", help="Apply autofixes where possible.")
    args = parser.parse_args()

    steps: list[tuple[str, str, list[str], tuple[int, ...]]] = [
        (
            "Ruff Lint",
            "blue",
            ["uv", "run", "ruff", "check", "."] + (["--fix"] if args.fix else []),
            (0,),
        ),
        (
            "Ruff Format",
            "yellow",
            ["uv", "run", "ruff", "format", "."] + ([] if args.fix else ["--check"]),
            (0,),
        ),
        ("Type Check", "green", ["uv", "run", "ty", "check", "."], (0,)),
    ]
    if not args.no_tests:
        # Wall-clock budget tests run in their own serial pass so parallel load cannot break their timing.
        # PYTEST_ADDOPTS may add "-n N"; "-n 0" given here overrides it for the second pass.
        # Exit code 5 means no test was selected, which is expected when --no-integration-tests empties the pass.
        integration_filter = " and not integration" if args.no_integration_tests else ""
        steps.append(
            (
                "Tests",
                "magenta",
                ["uv", "run", "pytest", "-m", f"not wall_clock and not real_server{integration_filter}"],
                (0,),
            )
        )
        steps.append(
            (
                "Wall-clock tests (serial)",
                "magenta",
                ["uv", "run", "pytest", "-m", f"wall_clock{integration_filter}", "-n", "0"],
                (0, 5) if args.no_integration_tests else (0,),
            )
        )
        # Real-server tests start Docker containers, so they run serially after the parallel pass.
        # All of them are integration tests, hence the step is skipped with --no-integration-tests.
        if not args.no_integration_tests:
            steps.append(
                (
                    "Real-server tests (serial)",
                    "magenta",
                    ["uv", "run", "pytest", "-m", "real_server", "-n", "0"],
                    (0,),
                )
            )

    failed = [label for label, style, cmd, ok_codes in steps if not run_step(label, style, cmd, ok_codes)]

    console.print()
    if not failed:
        console.print("[bold green]All checks passed.[/]")
    else:
        console.print("[bold red]One or more checks failed.[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
