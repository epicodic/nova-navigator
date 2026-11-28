# Development

This page covers working on Nova Navigator from a source checkout.
For installing and using the application, see the [README](https://github.com/epicodic/nova-navigator/blob/main/README.md).

## Setup

```sh
git clone https://github.com/epicodic/nova-navigator.git
cd nova-navigator
uv sync
uv run nn
```

`uv sync` installs the development environment, including the `azure` extra and the local `devtools/` project.
`uv run` also syncs the environment, so `uv sync` is optional.
The tests need `zsh` installed (`sudo apt-get install zsh`), because the stub SSH server runs commands with it.

## Tests

```sh
uv run pytest                                   # all tests
uv run pytest tests/test_archives.py            # single file
uv run pytest tests/test_archives.py::test_foo  # single test
```

Every test has a limit of 120 s.
A timeout ends a run without `-n` after printing all stacks.
With `-n` only that worker is lost, the test is reported by name, and the run continues.

Run the tests in parallel, then run the wall-clock tests serially:

```sh
uv run pytest -n 4 --dist worksteal -m "not wall_clock"
uv run pytest -m wall_clock
```

Wall-clock budget tests carry `@pytest.mark.wall_clock`, because they assert an elapsed-time budget that parallel load can break.
New elapsed-time assertions must carry the marker.

Tests marked `real_server` start pinned Docker containers, so they need a running Docker daemon.
See [testing.md](testing.md) for the real-server tests.

## Quality checks

```sh
uv run qa                 # all QA checks: lint, format check, type check, tests
uv run qa --fix           # same, with auto-fixes applied where possible
uv run qa --no-tests      # only lint, format check and type check
```

`uv run qa` runs the quality checks first, then three test passes:

1. The tests that are neither `wall_clock` nor `real_server`.
   They run in parallel only when `PYTEST_ADDOPTS="-n 4 --dist worksteal"` is set, as in CI.
   A bare `uv run qa` runs them serially.
2. The `wall_clock` tests, serially with `-n 0`.
3. The `real_server` tests, serially with `-n 0`, because they start Docker containers.
   `--no-integration-tests` skips this pass.

Individual tools:

```sh
uv run ruff check .   # lint
uv run ruff format .  # format
uv run ty check .     # type check
```

## Install check

```sh
uv run install_check              # build, then install the wheel and the sdist into empty environments
uv run install_check --dist DIR   # check an existing build in DIR
```

The install check starts `nn` and `ned` from each installed environment.

## Continuous integration

The `qa` job in `.github/workflows/ci.yml` runs on Ubuntu 24.04 and 26.04 with Python 3.12, 3.13 and 3.14.
Each job installs `zsh`, prepares the Docker images, runs `uv run qa` with `PYTEST_ADDOPTS="-n 4 --dist worksteal -v"`, and then runs `uv run install_check`.
Each job is stopped after 15 minutes.
