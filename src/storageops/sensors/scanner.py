"""Recursive, read-only file-system scanner: the agent's primary sensor."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from storageops.config import ScannerConfig
from storageops.models import FileRecord, ScanIssue
from storageops.sensors.metadata import build_record, extension_of, is_cloud_only, is_hidden

ProgressCallback = Callable[[int, str], None]
"""Called as ``progress(files_found, current_directory)`` while scanning."""


@dataclass(frozen=True)
class ScanStats:
    files_found: int
    files_skipped: int
    dirs_visited: int
    dirs_skipped: int
    duration_s: float


@dataclass
class ScanResult:
    root: str
    started_at: float  # epoch seconds
    records: list[FileRecord]
    issues: list[ScanIssue]
    stats: ScanStats


class FileScanner:
    """Walks a directory tree and turns every eligible file into a :class:`FileRecord`.

    The scanner never modifies the file system. Unreadable directories and files
    are recorded as :class:`ScanIssue` instead of aborting the scan. Symlinks and
    NTFS junctions are not followed unless configured, which avoids cycles and
    counting the same bytes twice. Cloud-only placeholder files are skipped
    because they use no local space.
    """

    def __init__(
        self,
        config: ScannerConfig | None = None,
        *,
        progress: ProgressCallback | None = None,
        now: float | None = None,
    ) -> None:
        self.config = config or ScannerConfig()
        self._progress = progress
        self._now = now  # injectable clock for deterministic ages in tests

    def scan(self, root: str | os.PathLike[str]) -> ScanResult:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            raise FileNotFoundError(f"Scan root does not exist: {root_path}")
        if not root_path.is_dir():
            raise NotADirectoryError(f"Scan root is not a directory: {root_path}")

        started_at = time.time()
        clock = time.perf_counter()
        now = self._now if self._now is not None else started_at

        records: list[FileRecord] = []
        issues: list[ScanIssue] = []
        files_skipped = dirs_visited = dirs_skipped = 0
        visited_real: set[str] = set()  # cycle guard, only needed when following links
        root_str = str(root_path)
        stack: list[tuple[str, int]] = [(root_str, 0)]

        while stack:
            directory, depth = stack.pop()
            if self.config.follow_symlinks:
                real = os.path.realpath(directory)
                if real in visited_real:
                    dirs_skipped += 1
                    continue
                visited_real.add(real)

            try:
                entries = os.scandir(directory)
            except OSError as exc:
                issues.append(ScanIssue(directory, _describe(exc)))
                continue
            dirs_visited += 1

            with entries:
                for entry in entries:
                    try:
                        if not self.config.follow_symlinks and _is_link(entry):
                            if entry.is_dir():
                                dirs_skipped += 1
                            else:
                                files_skipped += 1
                            continue
                        if entry.is_dir():
                            if self._should_descend(entry, depth):
                                stack.append((entry.path, depth + 1))
                            else:
                                dirs_skipped += 1
                            continue
                        if not entry.is_file():  # sockets, devices, broken links
                            files_skipped += 1
                            continue
                        st = entry.stat()
                        if not self._accept_file(entry.name, st):
                            files_skipped += 1
                            continue
                        records.append(build_record(entry.path, st, depth, now, root_str))
                    except OSError as exc:
                        issues.append(ScanIssue(entry.path, _describe(exc)))
                        continue

                    if self._progress and len(records) % self.config.progress_interval == 0:
                        self._progress(len(records), directory)

        stats = ScanStats(
            files_found=len(records),
            files_skipped=files_skipped,
            dirs_visited=dirs_visited,
            dirs_skipped=dirs_skipped,
            duration_s=time.perf_counter() - clock,
        )
        return ScanResult(root_str, started_at, records, issues, stats)

    def _should_descend(self, entry: os.DirEntry[str], depth: int) -> bool:
        cfg = self.config
        if entry.name.lower() in cfg.excluded_dir_names:
            return False
        if cfg.max_depth is not None and depth + 1 > cfg.max_depth:
            return False
        return cfg.include_hidden or not is_hidden(entry.name, entry.stat())

    def _accept_file(self, name: str, st: os.stat_result) -> bool:
        cfg = self.config
        if is_cloud_only(st):
            return False
        if st.st_size < cfg.min_file_size_bytes:
            return False
        if not cfg.include_hidden and is_hidden(name, st):
            return False
        return extension_of(name) not in cfg.excluded_extensions


def _is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+
    return bool(is_junction and is_junction())


def _describe(exc: OSError) -> str:
    return f"{type(exc).__name__}: {exc.strerror or exc}"
