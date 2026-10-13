import os

import pytest

from storageops.models import FileCategory
from storageops.sensors.metadata import build_record, classify, extension_of
from tests.conftest import NOW, write_file


@pytest.mark.parametrize(
    ("name", "parents", "expected"),
    [
        ("photo.JPG", (), FileCategory.IMAGE),
        ("movie.mkv", ("Videos",), FileCategory.VIDEO),
        ("archive.tar.gz", (), FileCategory.COMPRESSED),
        ("setup_tool.exe", (), FileCategory.INSTALLER),
        ("tool.exe", (), FileCategory.EXECUTABLE),
        ("README", (), FileCategory.OTHER),
        ("image.png", ("C:\\", "Users", "me", "AppData", "Local", "Temp"), FileCategory.TEMPORARY),
        ("data_0", ("Chrome", "Cache"), FileCategory.CACHE),
        ("run.log", ("cache", "logs"), FileCategory.LOG),  # nearest directory wins
    ],
)
def test_classify(name, parents, expected):
    assert classify(name, parents) is expected


def test_extension_of():
    assert extension_of("Report.PDF") == ".pdf"
    assert extension_of("archive.tar.gz") == ".gz"
    assert extension_of(".gitignore") == ""
    assert extension_of("Makefile") == ""


def test_build_record_computes_ages_and_flags(tmp_path):
    path = write_file(tmp_path / ".env", b"SECRET=1", age_days=400)
    record = build_record(str(path), os.stat(path), depth=0, now=NOW)

    assert record.name == ".env"
    assert record.size_bytes == 8
    assert record.age_days == pytest.approx(400)
    assert record.idle_days == pytest.approx(400)
    assert record.is_hidden
    assert not record.is_duplicate and record.duplicate_group is None


def test_future_timestamps_clamp_to_zero_age(tmp_path):
    path = write_file(tmp_path / "f.txt", b"x", age_days=-5)
    record = build_record(str(path), os.stat(path), depth=0, now=NOW)
    assert record.age_days == 0.0
