"""Nothing StorageOps does may happen without the user's OK: session, CLI and GUI."""

import os

import pytest

from storageops.cli import main
from storageops.config import Config, SafetyConfig
from storageops.gui.session import Session, WorkflowError
from tests.conftest import write_file

CONFIG = Config(safety=SafetyConfig(protected_paths=[]))


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "Downloads"
    for i in range(3):
        write_file(root / f"setup_{i}.msi", b"x" * 50_000 + bytes([i]), 500)
    return root


# ------------------------------------------------------------------ session


def test_every_session_step_requires_the_flag(env, tmp_path):
    session = Session(CONFIG, journal_dir=tmp_path / "journal")
    with pytest.raises(TypeError):  # forgetting to ask is a programming error
        session.load(env)
    with pytest.raises(WorkflowError, match="needs the user's OK"):
        session.load(env, user_ok=False)
    assert session.snapshot is None

    session.load(env, user_ok=True)
    with pytest.raises(WorkflowError, match="needs the user's OK"):
        session.plan(60_000, user_ok=False)
    assert session.result is None

    session.plan(60_000, user_ok=True)
    session.approve_all()
    with pytest.raises(WorkflowError, match="needs the user's OK"):
        session.replan_without([session.proposed[0].path], user_ok=False)
    with pytest.raises(WorkflowError, match="needs the user's OK"):
        session.execute(dry_run=True, user_ok=False)
    assert not session.can_execute  # the refused dry run did not count
    session.execute(dry_run=True, user_ok=True)
    with pytest.raises(WorkflowError, match="needs the user's OK"):
        session.execute(dry_run=False, user_ok=False)
    assert all(os.path.exists(a.path) for a in session.proposed)
    assert not list((tmp_path / "journal").glob("*[0-9].jsonl"))  # no execution journal


def test_truthy_values_are_not_an_ok(env, tmp_path):
    session = Session(CONFIG, journal_dir=tmp_path / "journal")
    with pytest.raises(WorkflowError):
        session.load(env, user_ok="yes")  # only the literal True counts


def test_refusals_are_logged(env, tmp_path):
    from storageops.activity_log import log_file, setup_logging

    setup_logging()
    session = Session(CONFIG, journal_dir=tmp_path / "journal")
    with pytest.raises(WorkflowError):
        session.load(env, user_ok=False)
    session.load(env, user_ok=True)
    text = log_file().read_text(encoding="utf-8")
    assert "refused load" in text and "user confirmed: load" in text


# ------------------------------------------------------------------ CLI


def test_cli_refuses_without_a_terminal_or_yes(env, tmp_path, capsys):
    out = tmp_path / "scan.csv"
    assert main(["scan", str(env), "-o", str(out), "-q"]) == 1
    err = capsys.readouterr().err
    assert "StorageOps will:" in err and "--yes" in err
    assert not out.exists()


@pytest.mark.parametrize(("answer", "code"), [("n", 1), ("", 1), ("y", 0), ("YES", 0)])
def test_cli_interactive_prompt(env, tmp_path, monkeypatch, capsys, answer, code):
    out = tmp_path / "plan.csv"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert main(["plan", str(env), "--free", "60KB", "-o", str(out), "-q"]) == code
    assert out.exists() == (code == 0)
    if code:
        assert "Cancelled. Nothing was done." in capsys.readouterr().err


def test_bench_asks_before_writing_results(tmp_path):
    out = tmp_path / "results"
    assert main(["bench", "--quick", "-o", str(out)]) == 1
    assert not out.exists()


# ------------------------------------------------------------------ GUI


@pytest.fixture
def gui(tmp_path, monkeypatch, env):
    tk = pytest.importorskip("tkinter")
    from storageops.gui import app as app_module

    try:
        window = app_module.StorageOpsApp(
            Session(CONFIG, journal_dir=tmp_path / "journal"), source=str(env)
        )
    except tk.TclError as exc:
        pytest.skip(f"no display available: {exc}")
    window.withdraw()
    answers = []
    monkeypatch.setattr(app_module.messagebox, "askokcancel",
                        lambda *a, **k: answers.pop(0) if answers else False)  # fmt: skip
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **k: None)
    window.answers = answers
    yield window
    window.destroy()


def pump(window):
    import time

    deadline = time.time() + 30
    window.update()
    while window.busy and time.time() < deadline:
        window.update()
        time.sleep(0.01)
    window.update()


def test_gui_does_not_scan_on_launch_and_respects_cancel(gui):
    pump(gui)
    assert gui.session.snapshot is None  # source pre-filled, nothing scanned
    assert "Press Scan / Load" in gui.status_var.get()

    gui._load()  # user presses Cancel
    pump(gui)
    assert gui.session.snapshot is None
    assert "cancelled. Nothing was done." in gui.status_var.get()

    gui.answers.append(True)
    gui._load()
    pump(gui)
    assert gui.session.snapshot is not None

    gui.size_var.set("60KB")
    gui._plan()  # Cancel
    pump(gui)
    assert gui.session.result is None

    gui.answers.append(True)
    gui._plan()
    pump(gui)
    assert gui.session.result is not None

    gui._approve_all()
    gui._execute(dry_run=True)  # Cancel
    pump(gui)
    assert gui.session.last_report is None and not gui.session.can_execute
