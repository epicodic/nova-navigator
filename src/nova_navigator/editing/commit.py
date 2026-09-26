"""Backend-specific staged replacement for external editing sessions."""

import hashlib
import os
import shutil
import stat
import tempfile
from http import HTTPStatus
from pathlib import Path
from uuid import uuid4

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from paramiko import SFTPClient, SSHException

from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath

from .model import SourceConflictError, SourceVersion
from .transfer import CHUNK_SIZE, digest_file, digest_vpath


def _check_digest(actual: str, expected: str) -> None:
    if actual != expected:
        raise SourceConflictError("Source content changed before replacement")


def _require_digest(expected: SourceVersion) -> None:
    if expected.kind != "digest":
        raise ValueError("Local and SSH commits require a content digest")


def _rename_ssh_stage(client: SFTPClient, stage: str, source: VPath, baseline: str, uploaded_digest: str) -> None:
    """Report what can be verified if the final SSH rename fails."""
    try:
        client.posix_rename(stage, source.path.as_posix())
    except (OSError, EOFError, SSHException) as error:
        outcome = "unverifiable"
        try:
            current = digest_vpath(source, lambda: False)
            if current == uploaded_digest:
                outcome = "replaced"
            elif current == baseline:
                outcome = "unchanged"
        except (OSError, EOFError, SSHException) as verification_error:
            error.add_note(f"Source verification failed: {verification_error}")
        raise OSError(f"SSH replacement failed; source {outcome}: {error}") from error


def commit_local(source: Path, replacement: Path, expected: SourceVersion, force: bool) -> None:
    """Copy to a sibling stage and replace after a fresh content comparison.

    Explicit overwrite captures the current version before staging. The final
    comparison still rejects changes during staging. The check and rename are
    separate operations, so another writer can race the final replacement.
    """
    _require_digest(expected)
    if source.is_symlink():
        raise ValueError("Cannot commit to a symlink source")
    observed = digest_file(source, lambda: False)
    baseline = observed if force else expected.value
    _check_digest(observed, baseline)
    stage: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".nova-edit-", dir=source.parent, delete=False) as output:
            stage = Path(output.name)
            with replacement.open("rb") as reader:
                shutil.copyfileobj(reader, output, CHUNK_SIZE)
            output.flush()
            os.fchmod(output.fileno(), stat.S_IMODE(source.stat().st_mode))
            os.fsync(output.fileno())
        if source.is_symlink():
            raise ValueError("Cannot commit to a symlink source")
        _check_digest(digest_file(source, lambda: False), baseline)
        os.replace(stage, source)
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)


def commit_ssh(source: VPath, replacement: Path, expected: SourceVersion, force: bool) -> None:
    """Upload a sibling and replace with the server's POSIX rename extension.

    There is no fallback to deleting or truncating the source. As with local
    replacement, the digest check cannot exclude a writer racing the rename.
    The original VPath and its saved-connection URI are never modified.
    """
    _require_digest(expected)
    filesystem = source.filesystem.unwrap()
    if not isinstance(filesystem, SSHFilesystem):
        raise TypeError("SSH commit requires an SSH filesystem")
    concrete = VPath(source.path, filesystem)
    client = filesystem._sftp_client
    mode = client.lstat(concrete.path.as_posix()).st_mode
    if mode is not None and stat.S_ISLNK(mode):
        raise ValueError("Cannot commit to a symlink source")
    observed = digest_vpath(concrete, lambda: False)
    baseline = observed if force else expected.value
    _check_digest(observed, baseline)
    stage = concrete.path.parent / f".nova-edit-{uuid4().hex}"
    stage_created = False
    uploaded_digest = hashlib.sha256()
    try:
        with client.open(stage.as_posix(), "wx") as output:
            stage_created = True
            client.chmod(stage.as_posix(), 0o600)
            with replacement.open("rb") as reader:
                while chunk := reader.read(CHUNK_SIZE):
                    output.write(chunk)
                    uploaded_digest.update(chunk)
        mode = client.stat(concrete.path.as_posix()).st_mode
        if mode is not None:
            client.chmod(stage.as_posix(), stat.S_IMODE(mode))
        mode = client.lstat(concrete.path.as_posix()).st_mode
        if mode is not None and stat.S_ISLNK(mode):
            raise ValueError("Cannot commit to a symlink source")
        _check_digest(digest_vpath(concrete, lambda: False), baseline)
        _rename_ssh_stage(client, stage.as_posix(), concrete, baseline, uploaded_digest.hexdigest())
        stage_created = False
        filesystem.refresh(concrete.parent)
    except BaseException as error:
        if stage_created:
            try:
                client.remove(stage.as_posix())
            except FileNotFoundError:
                pass
            except (OSError, EOFError, SSHException) as cleanup_error:
                error.add_note(f"Could not remove remote stage {stage}: {cleanup_error}")
        raise


def commit_azure(source: VPath, replacement: Path, expected: SourceVersion, force: bool) -> None:
    """Stream a replacement with an ETag condition, including explicit overwrites."""
    if expected.kind != "etag":
        raise ValueError("Azure commits require an ETag")
    filesystem = source.filesystem.unwrap()
    if not isinstance(filesystem, AzureFilesystem):
        raise TypeError("Azure commit requires an Azure filesystem")
    concrete = VPath(source.path, filesystem)
    blob = filesystem._client.get_blob_client(concrete.path.as_posix().lstrip("/"))
    etag = blob.get_blob_properties().etag if force else expected.value
    if not isinstance(etag, str):
        raise ValueError("Azure source has no ETag")
    try:
        with replacement.open("rb") as reader:
            blob.upload_blob(reader, overwrite=True, etag=etag, match_condition=MatchConditions.IfNotModified)
    except HttpResponseError as error:
        if error.status_code == HTTPStatus.PRECONDITION_FAILED:
            raise SourceConflictError("Source changed before Azure replacement") from error
        raise
    filesystem.refresh(concrete.parent)
