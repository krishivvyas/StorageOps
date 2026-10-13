import pandas as pd
import pytest

from storageops.sensors.dataset import (
    COLUMNS,
    category_summary,
    duplicate_summary,
    load_dataset,
    records_to_frame,
    save_dataset,
)
from storageops.sensors.hashing import find_duplicates
from storageops.sensors.scanner import FileScanner
from tests.conftest import NOW, REPORT_BYTES, write_file


@pytest.fixture
def frame(sample_tree):
    write_file(sample_tree / "NA", b"literal NA name, no extension", 1)
    records, _ = find_duplicates(FileScanner(now=NOW).scan(sample_tree).records)
    return records_to_frame(records)


def test_frame_schema(frame):
    assert tuple(frame.columns) == COLUMNS
    assert frame["size_bytes"].dtype == "int64"
    assert frame["duplicate_group"].dtype == "Int64"
    assert isinstance(frame["category"].dtype, pd.CategoricalDtype)


def test_empty_frame_keeps_schema():
    empty = records_to_frame([])
    assert tuple(empty.columns) == COLUMNS and empty.empty


@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_round_trip(frame, tmp_path, suffix):
    if suffix == ".parquet":
        pytest.importorskip("pyarrow")
    path = save_dataset(frame, tmp_path / "out" / f"scan{suffix}")
    pd.testing.assert_frame_equal(load_dataset(path), frame)


def test_unsupported_format(frame, tmp_path):
    with pytest.raises(ValueError):
        save_dataset(frame, tmp_path / "scan.xlsx")


def test_category_summary(frame):
    summary = category_summary(frame)
    assert summary["total_bytes"].sum() == frame["size_bytes"].sum()
    assert summary["share"].sum() == pytest.approx(1.0)
    assert summary["files"].sum() == len(frame)
    documents = summary.set_index("category").loc["document"]
    assert documents["duplicate_files"] == 2


def test_duplicate_summary(frame):
    groups = duplicate_summary(frame)
    assert len(groups) == 1
    row = groups.iloc[0]
    assert row["copies"] == 3
    assert row["reclaimable_bytes"] == 2 * len(REPORT_BYTES)
    assert row["original_path"].endswith("report_old.pdf")


def test_duplicate_summary_without_duplicates():
    assert duplicate_summary(records_to_frame([])).empty
