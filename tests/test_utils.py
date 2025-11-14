import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path, PurePath

DATA_DIR = Path(__file__).parent / "data"


def create_file(path: PurePath, content: str) -> None:
    # create parent directories if they don't exist
    os.makedirs(path.parent, exist_ok=True)

    with open(path, "x") as f:
        f.write(content)


def check_file(path: PurePath, expected_content: str) -> None:
    assert os.path.isfile(path), f"File {path} does not exist"
    with open(path) as f:
        actual_content = f.read()

    assert actual_content == expected_content, f"Content mismatch in {path}"


@contextmanager
def temporary_directory(keep: bool = False) -> Generator[PurePath]:
    path = tempfile.mkdtemp()
    try:
        yield PurePath(path)
    finally:
        if not keep:
            shutil.rmtree(path)
