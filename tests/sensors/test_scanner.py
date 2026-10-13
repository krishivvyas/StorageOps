import os

import pytest

from storageops.config import ScannerConfig
from storageops.models import FileCategory
from storageops.sensors.scanner import FileScanner
from tests.conftest import NOW, write_file

ALL_FILES = {
    "empty.txt",
    "setup_tool.exe",
    "report.pdf",
    "report (1).pdf",
    "report_old.pdf",
    "photo.jpg",
    "photo_edit.jpg",
    "session.tmp",
}


def scan(root, **cfg):
    return FileScanner(ScannerConfig(**cfg), now=NOW).scan(root)


def test_finds_all_files_and_skips_excluded_dirs(sample_tree):
    result = scan(sample_tree)

    assert {r.name for r in result.records} == ALL_FILES  # nothing from .git
    assert result.stats.files_found == len(ALL_FILES)
    assert result.stats.dirs_skipped == 1
    assert not result.issues
    assert result.root == str(sample_tree.resolve())


def test_records_depth_relative_to_root(sample_tree):
    depth = {r.name: r.depth for r in scan(sample_tree).records}
    assert depth["empty.txt"] == 0
    assert depth["report.pdf"] == 1
    assert depth["report_old.pdf"] == 2


def test_directory_hints_ignore_folders_above_scan_root(tmp_path):
    write_file(tmp_path / "Temp" / "project" / "photo.jpg", b"x", 1)

    (inside,) = scan(tmp_path / "Temp" / "project").records
    assert inside.category is FileCategory.IMAGE
    (as_root,) = scan(tmp_path / "Temp").records  # the root's own name does count
    assert as_root.category is FileCategory.TEMPORARY


def test_max_depth_zero_scans_root_only(sample_tree):
    names = {r.name for r in scan(sample_tree, max_depth=0).records}
    assert names == {"empty.txt", "setup_tool.exe"}


def test_min_size_and_excluded_extensions(sample_tree):
    names = {
        r.name
        for r in scan(sample_tree, min_file_size_bytes=1, excluded_extensions=["TMP"]).records
    }
    assert "empty.txt" not in names
    assert "session.tmp" not in names


def test_hidden_files_respect_config(sample_tree):
    write_file(sample_tree / ".env", b"x", 1)
    write_file(sample_tree / ".config" / "settings.json", b"{}", 1)

    assert {".env", "settings.json"} <= {r.name for r in scan(sample_tree).records}
    hidden_off = {r.name for r in scan(sample_tree, include_hidden=False).records}
    assert ".env" not in hidden_off
    assert "settings.json" not in hidden_off


def test_progress_callback_is_invoked(sample_tree):
    calls = []
    FileScanner(ScannerConfig(progress_interval=1), progress=lambda n, d: calls.append(n)).scan(
        sample_tree
    )
    assert calls == list(range(1, len(ALL_FILES) + 1))


def test_symlinks_are_not_followed_by_default(sample_tree, tmp_path):
    outside = write_file(tmp_path / "outside" / "big.bin", b"0" * 1000, 1)
    try:
        os.symlink(outside.parent, sample_tree / "link_dir", target_is_directory=True)
        os.symlink(outside, sample_tree / "link_file.bin")
    except OSError:
        pytest.skip("creating symlinks requires extra privileges on this system")

    names = {r.name for r in scan(sample_tree).records}
    assert "big.bin" not in names and "link_file.bin" not in names


def test_invalid_roots(tmp_path):
    with pytest.raises(FileNotFoundError):
        FileScanner().scan(tmp_path / "missing")
    file_root = write_file(tmp_path / "file.txt", b"x", 1)
    with pytest.raises(NotADirectoryError):
        FileScanner().scan(file_root)
