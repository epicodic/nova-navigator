"""Static checks of the distribution metadata."""

import tomllib
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent


def _load(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_only_product_commands_are_declared() -> None:
    assert set(_load(_ROOT / "pyproject.toml")["project"]["scripts"]) == {"nn", "ned"}


def test_distribution_holds_the_three_packages() -> None:
    backend = _load(_ROOT / "pyproject.toml")["tool"]["uv"]["build-backend"]
    assert backend["module-name"] == ["nova_navigator", "nova_editor", "nova_widgets"]


def test_metadata() -> None:
    project = _load(_ROOT / "pyproject.toml")["project"]
    assert project["name"] == "nova-navigator"
    assert project["version"] == "0.1.0"
    assert project["description"] != "Add your description here"
    assert project["license"] == "MIT"
    assert project["requires-python"] == ">=3.12"
    assert "Operating System :: POSIX :: Linux" in project["classifiers"]
    assert project["urls"]["Repository"] == "https://github.com/epicodic/nova-navigator"


def test_azure_is_an_extra_and_debugpy_is_development_only() -> None:
    data = _load(_ROOT / "pyproject.toml")
    runtime = " ".join(data["project"]["dependencies"])
    assert "azure" not in runtime
    assert "debugpy" not in runtime
    extra = " ".join(data["project"]["optional-dependencies"]["azure"])
    assert "azure-storage-blob" in extra
    assert "azure-identity" in extra
    assert "nova-navigator[azure]" in data["dependency-groups"]["dev"]


def test_development_commands_live_in_devtools() -> None:
    scripts = _load(_ROOT / "devtools" / "pyproject.toml")["project"]["scripts"]
    assert {"qa", "dialog_tester"} <= set(scripts)
    for target in scripts.values():
        module = target.split(":")[0]
        assert (_ROOT / "src" / Path(*module.split("."))).with_suffix(".py").is_file(), target
