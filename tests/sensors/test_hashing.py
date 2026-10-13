import os

import pytest

from storageops.config import HashingConfig
from storageops.sensors.hashing import find_duplicates
from storageops.sensors.scanner import FileScanner
from tests.conftest import NOW, PHOTO_BYTES, REPORT_BYTES, write_file


def scan_and_hash(root, **cfg):
    records = FileScanner(now=NOW).scan(root).records
    updated, report = find_duplicates(records, HashingConfig(**cfg))
    return {r.name: r for r in updated}, report


@pytest.mark.parametrize("partial_bytes", [64 * 1024, 16])  # whole-file vs. head/tail+full path
def test_identical_files_form_one_group(sample_tree, partial_bytes):
    by_name, report = scan_and_hash(sample_tree, partial_bytes=partial_bytes)

    copies = [by_name[n] for n in ("report.pdf", "report (1).pdf", "report_old.pdf")]
    assert len({c.duplicate_group for c in copies}) == 1
    assert copies[0].duplicate_group is not None
    assert len({c.content_hash for c in copies}) == 1

    # The oldest copy is retained as the original.
    assert not by_name["report_old.pdf"].is_duplicate
    assert by_name["report.pdf"].is_duplicate and by_name["report (1).pdf"].is_duplicate

    assert report.groups == 1
    assert report.duplicate_files == 2
    assert report.reclaimable_bytes == 2 * len(REPORT_BYTES)


@pytest.mark.parametrize("partial_bytes", [64 * 1024, 16])
def test_same_size_different_content_is_not_duplicate(sample_tree, partial_bytes):
    by_name, _ = scan_and_hash(sample_tree, partial_bytes=partial_bytes)
    assert by_name["photo.jpg"].duplicate_group is None
    assert by_name["photo_edit.jpg"].duplicate_group is None


def test_difference_in_the_middle_is_caught_by_full_hash(tmp_path):
    a = bytearray(PHOTO_BYTES)
    b = bytearray(PHOTO_BYTES)
    b[len(b) // 2] ^= 0xFF  # head and tail identical, middle differs
    write_file(tmp_path / "a.bin", bytes(a), 1)
    write_file(tmp_path / "b.bin", bytes(b), 1)

    by_name, report = scan_and_hash(tmp_path, partial_bytes=16)
    assert report.groups == 0
    assert by_name["a.bin"].duplicate_group is None


def test_empty_files_are_ignored(tmp_path):
    write_file(tmp_path / "a.txt", b"", 1)
    write_file(tmp_path / "b.txt", b"", 1)
    _, report = scan_and_hash(tmp_path)
    assert report.groups == 0 and report.files_hashed == 0


def test_hard_links_are_not_counted_as_reclaimable(sample_tree):
    try:
        os.link(sample_tree / "docs" / "report.pdf", sample_tree / "docs" / "hardlink.pdf")
    except OSError:
        pytest.skip("hard links not supported on this file system")

    _, report = scan_and_hash(sample_tree)
    assert report.duplicate_files == 2  # unchanged: the hard link frees nothing
    assert report.reclaimable_bytes == 2 * len(REPORT_BYTES)


def test_file_removed_before_hashing_is_reported_not_raised(sample_tree):
    records = FileScanner(now=NOW).scan(sample_tree).records
    (sample_tree / "docs" / "report (1).pdf").unlink()

    updated, report = find_duplicates(records)
    assert len(updated) == len(records)
    assert report.duplicate_files == 1
    assert any("report (1).pdf" in issue.path for issue in report.issues)


def test_input_order_and_untouched_records_are_preserved(sample_tree):
    records = FileScanner(now=NOW).scan(sample_tree).records
    updated, _ = find_duplicates(records)
    assert [r.path for r in updated] == [r.path for r in records]
    assert next(r for r in updated if r.name == "session.tmp") == next(
        r for r in records if r.name == "session.tmp"
    )
