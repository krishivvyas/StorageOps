"""Drive the real tkinter window (hidden) through the whole workflow."""

import os
import time

import pytest

from storageops.config import Config, SafetyConfig
from storageops.gui.session import Session
from tests.conftest import write_file

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def window():
    """One Tk root for the module: re-creating Tk repeatedly is flaky on some builds."""
    from storageops.gui import app as app_module

    try:
        win = app_module.StorageOpsApp(Session())
    except tk.TclError as exc:
        pytest.skip(f"no display available: {exc}")
    win.withdraw()
    yield win
    win.destroy()


@pytest.fixture
def app(window, tmp_path, monkeypatch):
    from storageops.gui import app as app_module

    root = tmp_path / "Downloads"
    for i in range(4):
        write_file(root / f"setup_{i}.msi", b"x" * 60_000 + bytes([i]), 500)
    write_file(root / "notes.docx", b"y" * 30_000, 2)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    window.session = Session(
        Config(safety=SafetyConfig(protected_paths=[])),
        journal_dir=tmp_path / "journal",
        trash=lambda p: os.replace(p, bin_dir / os.path.basename(p)),
    )
    for var, value in ((window.search_var, ""), (window.category_var, "All"),
                       (window.size_var, "1GB")):  # fmt: skip
        var.set(value)
    window._refresh_all()
    window.source_var.set(str(root))
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(app_module.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: errors.append(a))
    window.errors = errors
    return window


def wait(window, timeout=30.0):
    """Pump the Tk event loop until the background task has finished."""
    deadline = time.time() + timeout
    window.update()
    while window.busy and time.time() < deadline:
        window.update()
        time.sleep(0.01)
    window.update()
    assert not window.busy, "background task did not finish"
    assert not window.errors, window.errors


def button_enabled(button):
    return not button.instate(["disabled"])


def test_full_workflow_through_the_window(app):
    assert not button_enabled(app.plan_btn)  # nothing loaded yet

    app._load()
    wait(app)
    assert len(app.category_table.tree.get_children()) >= 2
    assert len(app.files_table.tree.get_children()) == 5
    assert button_enabled(app.plan_btn)

    app.size_var.set("100KB")
    app._plan()
    wait(app)
    rows = app.plan_table.tree.get_children()
    assert rows
    assert all(app.plan_table.row(r)["approved"] is False for r in rows)  # unapproved by default
    assert not button_enabled(app.dry_run_btn)
    assert not button_enabled(app.execute_btn)

    app._approve_all()
    assert button_enabled(app.dry_run_btn)
    assert not button_enabled(app.execute_btn)  # dry run first

    app._execute(dry_run=True)
    wait(app)
    assert "Dry run" in app.results_var.get()
    assert all(os.path.exists(p) for p in rows)
    assert button_enabled(app.execute_btn)

    app._execute(dry_run=False)
    wait(app)
    assert "Executed" in app.results_var.get()
    assert not any(os.path.exists(p) for p in rows)
    assert not button_enabled(app.plan_btn)  # re-scan required
    assert not button_enabled(app.execute_btn)


def test_toggling_a_row_updates_the_summary(app):
    app._load()
    wait(app)
    app.size_var.set("100KB")
    app._plan()
    wait(app)
    first = app.plan_table.tree.get_children()[0]
    app._set_approval([first], True)
    assert app.plan_table.row(first)["approved"] is True
    assert app.plan_table.tree.item(first, "values")[0] == "☑"
    assert "Approved 1 of" in app.approval_var.get()
    app._set_approval([first], False)
    assert "Approved 0 of" in app.approval_var.get()


def test_reject_and_replan(app):
    app._load()
    wait(app)
    app.size_var.set("100KB")
    app._plan()
    wait(app)
    first = app.plan_table.tree.get_children()[0]
    app.plan_table.tree.selection_set(first)
    app._reject_selected()
    wait(app)
    assert first not in app.plan_table.tree.get_children()
    assert "Rejected files kept: 1" in app.plan_summary_var.get()


def test_infeasible_target_is_explained(app):
    app._load()
    wait(app)
    app.size_var.set("10TB")
    app._plan()
    wait(app)
    assert "cannot be reached" in app.plan_summary_var.get()
    assert not app.plan_table.tree.get_children()


def test_files_filters(app):
    app._load()
    wait(app)
    app.search_var.set("notes")
    app._refresh_files()
    assert len(app.files_table.tree.get_children()) == 1
    app.search_var.set("")
    app.category_var.set("installer")
    app._refresh_files()
    assert len(app.files_table.tree.get_children()) == 4


def test_paths_are_shown_relative_to_the_scanned_folder(app):
    app._load()
    wait(app)
    root = app.session.snapshot.root
    first = app.files_table.tree.get_children()[0]
    assert first.startswith(root)  # rows keep the full path
    shown = app.files_table.tree.item(first, "values")[0]
    assert not shown.startswith(root) and first.endswith(shown)
