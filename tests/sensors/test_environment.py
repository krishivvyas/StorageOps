import pandas as pd

from storageops.sensors.environment import EnvironmentSnapshot, capture_environment
from tests.conftest import NOW, REPORT_BYTES


def test_capture_environment(sample_tree):
    snapshot = capture_environment(sample_tree, now=NOW)

    assert snapshot.root == str(sample_tree.resolve())
    assert len(snapshot.files) == snapshot.scan_stats.files_found == 8
    assert snapshot.duplicates.reclaimable_bytes == 2 * len(REPORT_BYTES)
    assert snapshot.disk.total_bytes > 0 and snapshot.disk.free_bytes > 0
    assert snapshot.total_bytes == snapshot.files["size_bytes"].sum()


def test_duplicate_detection_can_be_disabled(sample_tree):
    snapshot = capture_environment(sample_tree, detect_duplicates=False)
    assert snapshot.duplicates is None
    assert snapshot.files["duplicate_group"].isna().all()


def test_snapshot_save_and_load(sample_tree, tmp_path):
    snapshot = capture_environment(sample_tree, now=NOW)
    data_path, meta_path = snapshot.save(tmp_path / "scans" / "env.csv")

    assert data_path.exists() and meta_path.name == "env.meta.json"
    loaded = EnvironmentSnapshot.load(data_path)
    pd.testing.assert_frame_equal(loaded.files, snapshot.files)
    assert loaded.disk == snapshot.disk
    assert loaded.scan_stats == snapshot.scan_stats
    assert loaded.duplicates.reclaimable_bytes == snapshot.duplicates.reclaimable_bytes
    assert loaded.issues == snapshot.issues
