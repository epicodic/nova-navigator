# Real-server tests

The tests under `tests/real_servers/` run the SSH and Azure filesystems against real servers instead of stubs.
They start pinned Docker containers, so they need a running Docker daemon.

## What is started

- Two OpenSSH servers, one built from `tests/real_servers/images/gnu` (Debian) and one from `tests/real_servers/images/busybox` (Alpine).
  The two servers share a per-session Docker network, so one serves as the jump host for the other.
- The Azurite Azure Blob emulator, from the pinned image in `tests/real_servers/docker.py`.

The helpers in `docker.py` call the `docker` command line directly; there is no Docker SDK dependency.
Each server is started once per test process and cached.
Containers run with `--rm` and a self-expiry timeout, so a killed test process does not leave them running.
Container and network names carry the prefix `nova-rs-` and a random suffix.

## Images and pinning

Base images are pinned by digest in the Dockerfiles, and the Azurite image is pinned by digest in `docker.py`.
Distribution packages inside the images are not version-pinned, so a security update in the mirror does not break the build.
To update a base image, change its digest in the Dockerfile or in `docker.py`, then run `prepare` again and run the real-server tests.

Build the images and pull Azurite once before the first run:

```sh
uv run python -m tests.real_servers.docker prepare
```

`prepare` checks the Docker daemon, builds both SSH images, prints their OpenSSH versions and pulls the Azurite image.

If the environment variable `PYTHONSAFEPATH` is set, this command fails with `No module named 'tests'`.
Run it as `env -u PYTHONSAFEPATH uv run python -m tests.real_servers.docker prepare` in that case.

## Marker and test passes

Tests that need a container carry the `real_server` marker.
The marker is declared in `pyproject.toml`.

`uv run qa` runs three passes:

1. The unmarked tests, excluding `real_server` and `wall_clock`, in parallel when `PYTEST_ADDOPTS` sets `-n`, as in CI.
2. The `wall_clock` tests, serially.
3. The `real_server` tests, serially, because each one starts containers.

The third pass is skipped with `--no-integration-tests`.

## Skipped or failed

Without `CI` and without `NOVA_REQUIRE_REAL_SERVERS`, a test whose server cannot start is skipped with the reason.
With either variable set, the same test fails with `Real server required but unavailable`.
CI sets `CI`, so a missing Docker daemon turns a CI job red instead of passing silently.
Set `NOVA_REQUIRE_REAL_SERVERS=1` locally when a run must not skip anything:

```sh
NOVA_REQUIRE_REAL_SERVERS=1 uv run pytest -m real_server -n 0
```

## Host name for the URI test

The test that connects through an `azure://` URI uses the host name `<account>.blob.localhost`.
Most systems resolve every `*.localhost` name to the loopback address, but some resolvers do not.
If the name does not resolve, that one test fails with a message that names the host.
No other real-server test depends on this name.

## Proxies

The Azurite helper sets `NO_PROXY` for the loopback addresses, `localhost`, `.localhost` and the managed-identity address, so a proxy in the environment does not intercept the test traffic.
