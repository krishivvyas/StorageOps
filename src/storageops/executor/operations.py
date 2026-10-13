"""The actuators: one function per cleanup action.

Every operation verifies its result *before* anything is removed, and raises
:class:`OperationError` (leaving the original untouched) when it cannot do so safely.

=========  ==========================================================================
DELETE     Send to the Recycle Bin (recoverable; space returns when the bin is emptied).
ARCHIVE    Zip next to the original (or into ``archive_dir``), re-read the archive and
           compare its content hash with the original's, then remove the original.
COMPRESS   Windows NTFS transparent compression (``compact /c``); the file stays usable at
           its path. Its content hash is checked to be unchanged afterwards.
MOVE       Copy to the destination, compare content hashes, then remove the original.
=========  ==========================================================================
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from storageops.models import Action

Trash = Callable[[str], None]
_CHUNK = 1024 * 1024
_MTIME_TOLERANCE_S = 2.0  # FAT/exFAT timestamps have 2 s resolution


class OperationError(Exception):
    """The operation was refused or failed; the original file was left in place."""


@dataclass(frozen=True)
class ApprovedAction:
    """An action the user explicitly approved, plus what the file looked like when planned."""

    path: str
    action: Action
    size_bytes: int
    modified_ts: float
    expected_benefit: int = 0


@dataclass(frozen=True)
class OperationOutcome:
    bytes_freed: int
    artifact: str | None = None  # archive or destination path, if one was created
    message: str = ""


def file_digest(path: str | os.PathLike[str]) -> str:
    hasher = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_unchanged(item: ApprovedAction) -> os.stat_result:
    """Refuse to act on a file that is gone or was modified after the plan was made."""
    try:
        st = os.stat(item.path)
    except FileNotFoundError as exc:
        raise OperationError("file no longer exists") from exc
    if st.st_size != item.size_bytes or abs(st.st_mtime - item.modified_ts) > _MTIME_TOLERANCE_S:
        raise OperationError("file changed since it was scanned; re-scan and re-plan")
    return st


def unique_path(path: Path) -> Path:
    """``path``, or ``name (1).ext``, ``name (2).ext``, ... if that already exists."""
    if not path.exists():
        return path
    for n in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise OperationError(f"no free file name near {path}")


def same_volume(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


# --------------------------------------------------------------------------- actions


def delete(item: ApprovedAction, trash: Trash) -> OperationOutcome:
    trash(item.path)
    if os.path.exists(item.path):
        raise OperationError("the Recycle Bin did not accept the file")
    return OperationOutcome(item.size_bytes, message="moved to the Recycle Bin")


def archive(item: ApprovedAction, archive_dir: str | None = None) -> OperationOutcome:
    source = Path(item.path)
    folder = Path(archive_dir) if archive_dir else source.parent
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder / f"{source.name}.zip")
    digest = file_digest(source)
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.write(source, arcname=source.name)
        _verify_archive(target, source.name, item.size_bytes, digest)
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, OperationError):
            raise
        raise OperationError(f"archiving failed: {exc}") from exc

    _remove(source)
    archived = target.stat().st_size
    freed = item.size_bytes - archived if same_volume(target, source.parent) else item.size_bytes
    return OperationOutcome(max(freed, 0), str(target), f"archived and verified -> {target}")


def _verify_archive(target: Path, member: str, size: int, digest: str) -> None:
    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None:
            raise OperationError("archive failed its CRC check")
        if z.getinfo(member).file_size != size:
            raise OperationError("archived size does not match the original")
        hasher = hashlib.blake2b(digest_size=32)
        with z.open(member) as fh:
            while chunk := fh.read(_CHUNK):
                hasher.update(chunk)
    if hasher.hexdigest() != digest:
        raise OperationError("archived content does not match the original")


def compress(item: ApprovedAction) -> OperationOutcome:
    if sys.platform != "win32":
        raise OperationError("in-place compression is only supported on Windows (NTFS)")
    digest = file_digest(item.path)
    run = subprocess.run(
        ["compact", "/c", "/q", item.path], capture_output=True, text=True, check=False
    )
    if run.returncode != 0:
        raise OperationError(f"compact failed: {(run.stdout + run.stderr).strip()[:200]}")
    if file_digest(item.path) != digest:  # should be impossible; refuse to report success
        raise OperationError("content changed during compression")
    on_disk = compressed_size(item.path)
    if on_disk >= item.size_bytes:
        return OperationOutcome(0, message="file system did not compress this file")
    return OperationOutcome(item.size_bytes - on_disk, message=f"compressed to {on_disk:,} bytes")


def compressed_size(path: str) -> int:
    """Bytes the file actually occupies on an NTFS volume (Windows only)."""
    high = ctypes.c_ulong(0)
    get_size = ctypes.windll.kernel32.GetCompressedFileSizeW
    get_size.restype = ctypes.c_ulong
    low = get_size(ctypes.c_wchar_p(path), ctypes.byref(high))
    return (high.value << 32) + low


def move(item: ApprovedAction, destination: str | None) -> OperationOutcome:
    if not destination:
        raise OperationError("no MOVE destination configured")
    source = Path(item.path)
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder / source.name)
    digest = file_digest(source)
    try:
        shutil.copy2(source, target)
        if file_digest(target) != digest:
            raise OperationError("copied content does not match the original")
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, OperationError):
            raise
        raise OperationError(f"copy failed: {exc}") from exc
    _remove(source)
    freed = 0 if same_volume(target, source.parent) else item.size_bytes
    return OperationOutcome(freed, str(target), f"moved and verified -> {target}")


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError as exc:
        raise OperationError(
            f"result verified but the original could not be removed: {exc}"
        ) from exc
