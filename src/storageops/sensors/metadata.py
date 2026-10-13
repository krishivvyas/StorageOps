"""Per-file metadata extraction and category classification."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import PurePath

from storageops.models import FileCategory, FileRecord

SECONDS_PER_DAY = 86_400.0

# fmt: off
_EXTENSIONS: dict[FileCategory, tuple[str, ...]] = {
    FileCategory.DOCUMENT: (
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".ods",
        ".csv", ".ppt", ".pptx", ".odp", ".epub",
    ),
    FileCategory.IMAGE: (
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".raw",
        ".cr2", ".nef", ".svg", ".psd",
    ),
    FileCategory.VIDEO: (
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg",
    ),
    FileCategory.AUDIO: (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"),
    FileCategory.COMPRESSED: (
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".zst",
    ),
    FileCategory.INSTALLER: (
        ".msi", ".msix", ".appx", ".iso", ".img", ".dmg", ".pkg", ".deb", ".rpm", ".apk",
    ),
    FileCategory.EXECUTABLE: (".exe", ".bat", ".cmd", ".ps1", ".sh", ".com", ".jar"),
    FileCategory.SOURCE_CODE: (
        ".py", ".ipynb", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".js", ".ts", ".html",
        ".css", ".go", ".rs", ".rb", ".php", ".sql", ".kt", ".swift",
    ),
    FileCategory.DATA: (
        ".json", ".xml", ".yaml", ".yml", ".toml", ".db", ".sqlite", ".parquet", ".h5",
        ".pkl", ".npy", ".bin",
    ),
    FileCategory.TEMPORARY: (
        ".tmp", ".temp", ".bak", ".old", ".swp", ".crdownload", ".part", ".partial", ".dmp",
    ),
    FileCategory.LOG: (".log", ".etl", ".trace"),
    FileCategory.SYSTEM: (".sys", ".dll", ".drv", ".ini", ".lnk", ".inf", ".cab", ".mui"),
}
# fmt: on

_EXTENSION_CATEGORY: dict[str, FileCategory] = {
    ext: category for category, exts in _EXTENSIONS.items() for ext in exts
}

# A file inside one of these directories takes the directory's category,
# whatever its extension (a .jpg in a browser cache is cache, not a photo).
_DIRECTORY_HINTS: dict[str, FileCategory] = {
    "temp": FileCategory.TEMPORARY,
    "tmp": FileCategory.TEMPORARY,
    "cache": FileCategory.CACHE,
    ".cache": FileCategory.CACHE,
    "cache2": FileCategory.CACHE,
    "code cache": FileCategory.CACHE,
    "gpucache": FileCategory.CACHE,
    "__pycache__": FileCategory.CACHE,
    "logs": FileCategory.LOG,
}

_INSTALLER_KEYWORDS = ("setup", "install", "installer", "update")

# Cloud placeholders (e.g. OneDrive "online-only" files) occupy no local space and
# reading them would trigger a download.
_CLOUD_ONLY_ATTRIBUTES = (
    stat.FILE_ATTRIBUTE_OFFLINE
    | 0x0004_0000  # FILE_ATTRIBUTE_RECALL_ON_OPEN
    | 0x0040_0000  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


def extension_of(name: str) -> str:
    """Lower-case final suffix including the dot, or ``""`` (``".gitignore"`` has none)."""
    return PurePath(name).suffix.lower()


def classify(name: str, parent_parts: Iterable[str] = ()) -> FileCategory:
    """Assign a :class:`FileCategory` from the file name and its parent directories.

    The nearest parent directory with a known hint wins; otherwise the extension
    decides. Executables whose names look like installers are classed as installers.
    """
    for part in reversed(tuple(parent_parts)):
        hint = _DIRECTORY_HINTS.get(part.lower())
        if hint is not None:
            return hint

    ext = extension_of(name)
    if ext == ".exe" and any(k in name.lower() for k in _INSTALLER_KEYWORDS):
        return FileCategory.INSTALLER
    return _EXTENSION_CATEGORY.get(ext, FileCategory.OTHER)


def file_attributes(st: os.stat_result) -> int:
    """Windows file attribute bits (always 0 on other platforms)."""
    return getattr(st, "st_file_attributes", 0)


def is_hidden(name: str, st: os.stat_result) -> bool:
    return name.startswith(".") or bool(file_attributes(st) & stat.FILE_ATTRIBUTE_HIDDEN)


def is_cloud_only(st: os.stat_result) -> bool:
    return bool(file_attributes(st) & _CLOUD_ONLY_ATTRIBUTES)


def _days_since(timestamp: float, now: float) -> float:
    # Clamp: files with timestamps in the future (clock skew, copied media) count as new.
    return max(0.0, (now - timestamp) / SECONDS_PER_DAY)


def build_record(
    path: str, st: os.stat_result, depth: int, now: float, root: str | None = None
) -> FileRecord:
    """Build a :class:`FileRecord` from a path and its ``stat`` result.

    If ``root`` (the scan root) is given, directory hints are taken only from the
    root's own name and the folders below it. Scanning ``...\\Temp`` therefore
    still marks its files as temporary, but a project stored under
    ``D:\\tmp\\project`` is not.
    """
    p = PurePath(path)
    parents = p.parent.parts if root is None else p.relative_to(PurePath(root).parent).parent.parts
    attrs = file_attributes(st)
    created = getattr(st, "st_birthtime", st.st_ctime)
    last_touched = max(st.st_atime, st.st_mtime)
    return FileRecord(
        path=path,
        name=p.name,
        extension=extension_of(p.name),
        category=classify(p.name, parents),
        size_bytes=st.st_size,
        created_ts=created,
        modified_ts=st.st_mtime,
        accessed_ts=st.st_atime,
        age_days=_days_since(st.st_mtime, now),
        idle_days=_days_since(last_touched, now),
        depth=depth,
        is_hidden=is_hidden(p.name, st),
        is_system=bool(attrs & stat.FILE_ATTRIBUTE_SYSTEM),
        is_readonly=bool(attrs & stat.FILE_ATTRIBUTE_READONLY) or not st.st_mode & stat.S_IWRITE,
    )
