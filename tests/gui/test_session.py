import os

import pytest

from storageops.config import Config, SafetyConfig
from storageops.gui.session import Session, WorkflowError
from tests.conftest import write_file

CONFIG = Config(safety=SafetyConfig(protected_paths=[]))
BLOB = b"x" * 50_000


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "Downloads"
    for i in range(4):
        write_file(root / f"setup_{i}.msi", BLOB + bytes([i]), 500)  # old installers
    write_file(root / "report.docx", BLOB, 1)
    return root


@pytest.fixture
def session(tmp_path):
    return Session(CONFIG, journal_dir=tmp_path / "journal")


def trash_into(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    return lambda p: os.replace(p, bin_dir / os.path.basename(p))


def test_full_workflow(session, env, tmp_path):
    session.load(env, user_ok=True)
    result = session.plan(100_000, mode="free", user_ok=True)
    assert result.plan.is_goal
    assert session.approved == set()  # nothing is approved by default
    assert not session.can_execute

    with pytest.raises(WorkflowError):
        session.execute(dry_run=False, user_ok=True)  # no approval yet

    session.approve_all()
    summary = session.approval_summary()
    assert summary.actions == len(result.plan.actions) and summary.shortfall == 0
    with pytest.raises(WorkflowError):
        session.execute(dry_run=False, user_ok=True)  # approved, but no dry run yet

    report = session.execute(dry_run=True, user_ok=True)
    assert {r.status for r in report.results} == {"dry-run"}
    assert all(os.path.exists(r.path) for r in report.results)
    assert session.can_execute

    report = session.execute(dry_run=False, user_ok=True, trash=trash_into(tmp_path))
    assert report.counts()["done"] == len(result.plan.actions)
    assert session.needs_rescan and not session.can_execute
    with pytest.raises(WorkflowError):
        session.execute(dry_run=True, user_ok=True)
    assert len(list((tmp_path / "journal").glob("*.jsonl"))) == 2


def test_changing_the_selection_invalidates_the_dry_run(session, env):
    session.load(env, user_ok=True)
    session.plan(100_000, user_ok=True)
    session.approve_all()
    session.execute(dry_run=True, user_ok=True)
    assert session.can_execute
    first = next(iter(session.approved))
    session.set_approved([first], False)
    assert not session.can_execute


def test_a_new_plan_starts_unapproved(session, env):
    session.load(env, user_ok=True)
    session.plan(100_000, user_ok=True)
    session.approve_all()
    session.plan(150_000, user_ok=True)
    assert session.approved == set()


def test_partial_approval_reports_a_shortfall(session, env):
    session.load(env, user_ok=True)
    result = session.plan(140_000, user_ok=True)
    smallest = min(result.plan.actions, key=lambda a: a.benefit_bytes)
    session.set_approved([smallest.path])
    summary = session.approval_summary()
    assert summary.actions == 1 and summary.shortfall > 0


def test_replan_without_rejected_files(session, env):
    session.load(env, user_ok=True)
    first = session.plan(100_000, user_ok=True)
    rejected = first.plan.actions[0].path
    second = session.replan_without([rejected], user_ok=True)
    assert rejected not in {a.path for a in second.plan.actions}
    assert second.plan.is_goal
    session.clear_rejections()
    assert session.rejected == set()


def test_unknown_paths_cannot_be_approved(session, env):
    session.load(env, user_ok=True)
    session.plan(100_000, user_ok=True)
    session.set_approved([r"C:\not\in\plan.txt"])
    assert session.approved == set()


def test_actions_require_a_loaded_environment(session):
    with pytest.raises(WorkflowError):
        session.plan(1, user_ok=True)
    with pytest.raises(WorkflowError):
        session.replan_without([], user_ok=True)


def test_load_saved_dataset_and_plan_table(session, env, tmp_path):
    session.load(env, user_ok=True)
    data = tmp_path / "scan.csv"
    session.snapshot.save(data)
    fresh = Session(CONFIG, journal_dir=tmp_path / "j2")
    fresh.load(data, user_ok=True)
    fresh.plan(100_000, user_ok=True)
    table = fresh.plan_table()
    assert list(table.columns)[:3] == ["approved", "action", "path"]
    assert not table["approved"].any()
