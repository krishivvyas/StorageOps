import os
import sys
import zipfile

import pytest

from storageops.config import SafetyConfig
from storageops.executor import ApprovedAction, Executor, Journal, approve
from storageops.executor import operations as ops
from storageops.models import Action, CleanupPlan, PlannedAction
from storageops.sensors.dataset import records_to_frame
from storageops.sensors.scanner import FileScanner

TEXT = b"StorageOps test line, very compressible.\n" * 4000  # ~160 KB


@pytest.fixture
def workdir(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    return root


def make(path, data=TEXT):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def item(path, action, **kw):
    st = os.stat(path)
    return ApprovedAction(str(path), action, st.st_size, st.st_mtime, **kw)


class FakeTrash:
    def __init__(self, tmp_path):
        self.bin = tmp_path / "bin"
        self.bin.mkdir(exist_ok=True)
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        os.replace(path, self.bin / os.path.basename(path))


def executor(tmp_path, **kw):
    kw.setdefault("safety", SafetyConfig(protected_paths=[]))
    kw.setdefault("trash", FakeTrash(tmp_path))
    kw.setdefault("journal", Journal(tmp_path / "journal.jsonl"))
    return Executor(**kw)


def test_dry_run_is_the_default_and_changes_nothing(workdir, tmp_path):
    f = make(workdir / "a.log")
    ex = executor(tmp_path)
    assert ex.dry_run
    report = ex.run([item(f, Action.DELETE, expected_benefit=123)])
    assert f.exists()
    assert report.results[0].status == "dry-run"
    assert report.bytes_freed == 123  # the expected figure, nothing was actually freed
    assert ex.journal.entries()[0]["dry_run"] is True


def test_delete_goes_to_the_trash(workdir, tmp_path):
    f = make(workdir / "a.log")
    trash = FakeTrash(tmp_path)
    report = executor(tmp_path, dry_run=False, trash=trash).run([item(f, Action.DELETE)])
    assert report.results[0].status == "done"
    assert not f.exists() and trash.calls == [str(f)]
    assert report.bytes_freed == len(TEXT)


def test_archive_verifies_then_removes_original(workdir, tmp_path):
    f = make(workdir / "notes.txt")
    report = executor(tmp_path, dry_run=False).run([item(f, Action.ARCHIVE)])
    result = report.results[0]
    assert result.status == "done", result.message
    assert not f.exists()
    with zipfile.ZipFile(result.artifact) as z:
        assert z.read("notes.txt") == TEXT
    assert 0 < result.bytes_freed < len(TEXT)


def test_archive_into_a_separate_folder_avoids_name_clashes(workdir, tmp_path):
    f = make(workdir / "notes.txt")
    archive_dir = tmp_path / "archives"
    make(archive_dir / "notes.txt.zip", b"existing")
    report = executor(tmp_path, dry_run=False, archive_dir=str(archive_dir)).run(
        [item(f, Action.ARCHIVE)]
    )
    assert report.results[0].artifact == str(archive_dir / "notes.txt (1).zip")
    assert (archive_dir / "notes.txt.zip").read_bytes() == b"existing"


def test_failed_archive_verification_keeps_the_original(workdir, tmp_path, monkeypatch):
    f = make(workdir / "notes.txt")

    def corrupt(*args, **kwargs):
        raise ops.OperationError("archived content does not match the original")

    monkeypatch.setattr(ops, "_verify_archive", corrupt)
    report = executor(tmp_path, dry_run=False).run([item(f, Action.ARCHIVE)])
    assert report.results[0].status == "failed"
    assert f.read_bytes() == TEXT
    assert not list(workdir.glob("*.zip"))  # the bad archive was cleaned up


def test_move_copies_verifies_and_removes(workdir, tmp_path):
    f = make(workdir / "video.mp4")
    dest = tmp_path / "other_drive"
    report = executor(tmp_path, dry_run=False, move_destination=str(dest)).run(
        [item(f, Action.MOVE)]
    )
    result = report.results[0]
    assert result.status == "done", result.message
    assert not f.exists() and (dest / "video.mp4").read_bytes() == TEXT
    assert result.bytes_freed == 0  # same volume in this test: nothing freed, reported honestly


def test_move_without_destination_is_skipped(workdir, tmp_path):
    f = make(workdir / "video.mp4")
    report = executor(tmp_path, dry_run=False).run([item(f, Action.MOVE)])
    assert report.results[0].status == "skipped" and f.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS compression")
def test_compress_keeps_file_usable(workdir, tmp_path):
    f = make(workdir / "big.log", TEXT * 4)
    report = executor(tmp_path, dry_run=False).run([item(f, Action.COMPRESS)])
    result = report.results[0]
    if result.status == "failed" and "compact" in result.message:
        pytest.skip(f"volume does not support compression: {result.message}")
    assert result.status == "done", result.message
    assert f.read_bytes() == TEXT * 4
    assert result.bytes_freed >= 0


def test_changed_or_missing_files_are_skipped(workdir, tmp_path):
    changed = make(workdir / "changed.log")
    approved = item(changed, Action.DELETE)
    changed.write_bytes(TEXT + b"more")
    gone = make(workdir / "gone.log")
    approved_gone = item(gone, Action.DELETE)
    gone.unlink()

    report = executor(tmp_path, dry_run=False).run([approved, approved_gone])
    assert [r.status for r in report.results] == ["skipped", "skipped"]
    assert "changed since" in report.results[0].message
    assert "no longer exists" in report.results[1].message
    assert changed.exists()


def test_protected_locations_are_refused_even_if_approved(workdir, tmp_path):
    f = make(workdir / "a.log")
    ex = executor(tmp_path, dry_run=False, safety=SafetyConfig(protected_paths=[str(workdir)]))
    report = ex.run([item(f, Action.DELETE)])
    assert report.results[0].status == "skipped" and "protected" in report.results[0].message
    assert f.exists()


def test_failure_of_one_action_does_not_stop_the_rest(workdir, tmp_path):
    a, b = make(workdir / "a.log"), make(workdir / "b.log")

    def flaky(path):
        if path.endswith("a.log"):
            raise OSError("bin is full")
        os.remove(path)

    report = executor(tmp_path, dry_run=False, trash=flaky).run(
        [item(a, Action.DELETE), item(b, Action.DELETE)]
    )
    assert [r.status for r in report.results] == ["failed", "done"]
    assert a.exists() and not b.exists()
    assert len(Journal(tmp_path / "journal.jsonl").entries()) == 2


def test_cancel_stops_before_the_next_file(workdir, tmp_path):
    import threading

    files = [make(workdir / f"{i}.log") for i in range(3)]
    cancel = threading.Event()

    def stop_after_first(done, total, path):
        cancel.set()

    report = executor(tmp_path, dry_run=False).run(
        [item(f, Action.DELETE) for f in files], progress=stop_after_first, cancel=cancel
    )
    assert report.cancelled and len(report.results) == 1
    assert files[1].exists() and files[2].exists()


def test_approve_only_includes_selected_actions(workdir):
    a, b = make(workdir / "a.log"), make(workdir / "b.log")
    files = records_to_frame(FileScanner().scan(workdir).records)
    plan = CleanupPlan(
        actions=(
            PlannedAction(str(a), Action.DELETE, len(TEXT), len(TEXT), 0.1, 0.1),
            PlannedAction(str(b), Action.ARCHIVE, len(TEXT), 100, 0.1, 0.1),
        ),
        required_bytes=1,
        freed_bytes=len(TEXT) + 100,
        total_cost=0.2,
        meets_target=True,
        is_goal=True,
    )
    approved = approve(plan, files, {str(b)})
    assert [(x.path, x.action) for x in approved] == [(str(b), Action.ARCHIVE)]
    assert approved[0].modified_ts == pytest.approx(os.stat(b).st_mtime)
    assert approved[0].expected_benefit == 100
