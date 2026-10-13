import json
import os
from pathlib import Path

from storageops.activity_log import log_file, setup_logging
from storageops.cli import main
from storageops.config import Config, SafetyConfig
from storageops.gui.session import Session
from storageops.paths import journal_dir, log_dir, storageops_home
from tests.conftest import write_file


def test_tests_never_use_the_real_home(isolated_storageops_home):
    assert storageops_home() == isolated_storageops_home
    assert Path.home() / ".storageops" not in (storageops_home(), log_dir().parent)
    assert journal_dir().parent == log_dir().parent == isolated_storageops_home


def test_setup_is_idempotent_and_follows_home_changes(tmp_path, monkeypatch):
    first = setup_logging()
    assert setup_logging() == first
    monkeypatch.setenv("STORAGEOPS_HOME", str(tmp_path / "elsewhere"))
    second = setup_logging()
    assert second != first and second.parent == tmp_path / "elsewhere" / "logs"


def test_cli_commands_are_logged(sample_tree):
    assert main(["scan", "--yes", str(sample_tree), "-q"]) == 0
    text = log_file().read_text(encoding="utf-8")
    assert "session start" in text
    assert f"command: storageops scan --yes {sample_tree}" in text
    assert "scan started (read-only)" in text and "scan finished: 8 files" in text


def test_the_whole_workflow_is_logged_and_journaled(tmp_path):
    root = tmp_path / "Downloads"
    for i in range(3):
        write_file(root / f"setup_{i}.msi", b"x" * 40_000 + bytes([i]), 400)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    setup_logging()
    session = Session(
        Config(safety=SafetyConfig(protected_paths=[])),
        trash=lambda p: os.replace(p, bin_dir / os.path.basename(p)),
    )
    session.load(root, user_ok=True)
    session.plan(50_000, user_ok=True)
    session.approve_all()
    session.execute(dry_run=True, user_ok=True)
    report = session.execute(dry_run=False, user_ok=True)

    text = log_file().read_text(encoding="utf-8")
    for expected in ("loaded", "plan:", "user approved", "dry run of", "DRY-RUN DELETE",
                     "EXECUTION of", "DONE DELETE", "EXECUTION finished"):  # fmt: skip
        assert expected in text, expected

    journals = sorted(journal_dir().glob("*.jsonl"))
    assert len(journals) == 2  # dry run + execution, in the isolated home
    entries = [json.loads(line) for line in journals[-1].read_text().splitlines()]
    assert {e["status"] for e in entries} == {"done"}
    assert {e["path"] for e in entries} == {r.path for r in report.results}
