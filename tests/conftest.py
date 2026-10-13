"""Shared fixtures. Tests only ever touch synthetic trees under pytest's tmp_path."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

DAY = 86_400
NOW = 1_760_000_000.0  # fixed clock so file ages are deterministic

REPORT_BYTES = b"%PDF" + b"A" * 5000
PHOTO_BYTES = bytes(range(256)) * 40  # 10 240 bytes


@pytest.fixture(autouse=True)
def isolated_storageops_home(tmp_path_factory, monkeypatch):
    """Every test gets its own StorageOps home, so no test can ever write logs or journals
    into the real ``~/.storageops``."""
    home = tmp_path_factory.mktemp("storageops_home")
    monkeypatch.setenv("STORAGEOPS_HOME", str(home))
    yield home
    logger = logging.getLogger("storageops")
    for handler in list(logger.handlers):
        if getattr(handler, "_storageops", False):
            logger.removeHandler(handler)
            handler.close()


def write_file(path: Path, data: bytes, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    ts = NOW - age_days * DAY
    os.utime(path, (ts, ts))
    return path


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """A small storage environment:

    env/
      empty.txt                    0 B
      setup_tool.exe               installer by name
      docs/report.pdf              copy of REPORT (400 days old)
      docs/report (1).pdf          copy of REPORT (10 days old)
      backup/2023/report_old.pdf   copy of REPORT (900 days old -> retained original)
      media/photo.jpg              PHOTO
      media/photo_edit.jpg         same size as PHOTO, last byte differs -> not a duplicate
      Temp/session.tmp             temporary
      .git/objects/ab              copy of REPORT inside an excluded folder
    """
    root = tmp_path / "env"
    write_file(root / "empty.txt", b"", 50)
    write_file(root / "setup_tool.exe", b"MZ" + b"\x00" * 300, 200)
    write_file(root / "docs" / "report.pdf", REPORT_BYTES, 400)
    write_file(root / "docs" / "report (1).pdf", REPORT_BYTES, 10)
    write_file(root / "backup" / "2023" / "report_old.pdf", REPORT_BYTES, 900)
    write_file(root / "media" / "photo.jpg", PHOTO_BYTES, 30)
    write_file(root / "media" / "photo_edit.jpg", PHOTO_BYTES[:-1] + b"\x00", 5)
    write_file(root / "Temp" / "session.tmp", b"t" * 100, 2)
    write_file(root / ".git" / "objects" / "ab", REPORT_BYTES, 1)
    return root
