"""StorageOps desktop app (tkinter): scan, plan, review, approve, dry run, execute.

This module only displays a :class:`~storageops.gui.session.Session` and forwards
user input to it; the workflow rules live in the session. Long tasks (scanning,
planning, executing) run on a worker thread and report back through a queue, so
the window stays responsive.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from storageops.activity_log import log_file, setup_logging
from storageops.config import load_config
from storageops.formatting import format_bytes, parse_size
from storageops.gui.session import Session, WorkflowError, describe_action
from storageops.gui.widgets import INK, MUTED, CategoryChart, Column, Table
from storageops.models import Action, FileCategory
from storageops.planners import PLANNER_NAMES, plan_to_frame
from storageops.sensors import category_summary, records_to_frame

log = logging.getLogger(__name__)
FILES_SHOWN = 5000  # rows in the Files tab (largest first); filters narrow the set
PLANNER_LABELS = {
    "bnb": "Branch & bound",
    "greedy": "Greedy (fast)",
    "greedy-heuristic": "Greedy (heuristic)",
}
CHECKED, UNCHECKED = "☑", "☐"


def _count(v: Any) -> str:
    return f"{int(v):,}"


def _days(v: Any) -> str:
    return f"{float(v):,.0f}"


def _risk(v: Any) -> str:
    return f"{float(v):.2f}"


def _share(v: Any) -> str:
    return f"{float(v):.1%}"


class StorageOpsApp(tk.Tk):
    def __init__(self, session: Session | None = None, source: str | None = None) -> None:
        super().__init__()
        self.title("StorageOps - Safe Cleanup Planner")
        self.geometry("1280x820")
        self.minsize(1040, 660)
        self.session = session or Session()
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._busy = False
        self._cancel: threading.Event | None = None

        self._configure_style()
        self._build_controls()
        self._build_status()
        self._build_tabs()
        self._refresh_all()
        self.after(100, self._poll)
        if source:  # pre-filled only: nothing is scanned until the user presses OK
            self.source_var.set(source)
            self.status_var.set(f"Press Scan / Load to read {source}.")

    # ================================================================== layout

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        else:
            style.theme_use("clam")
        style.configure("Treeview", rowheight=22)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Summary.TLabel", foreground=INK, font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

    def _build_controls(self) -> None:
        bar = ttk.Frame(self, padding=(10, 8, 10, 4))
        bar.pack(fill="x")
        bar.columnconfigure(1, weight=1)

        ttk.Label(bar, text="Source").grid(row=0, column=0, sticky="w")
        self.source_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.source_var).grid(
            row=0, column=1, columnspan=4, sticky="ew", padx=6
        )
        ttk.Button(bar, text="Folder…", command=self._choose_folder).grid(row=0, column=5)
        ttk.Button(bar, text="Dataset…", command=self._choose_dataset).grid(row=0, column=6, padx=4)
        self.load_btn = ttk.Button(
            bar, text="Scan / Load", style="Accent.TButton", command=self._load
        )
        self.load_btn.grid(row=0, column=7, sticky="ew")

        ttk.Label(bar, text="Target").grid(row=1, column=0, sticky="w", pady=(8, 0))
        # One frame for the whole second row, so its widgets never overlap or get squeezed.
        target = ttk.Frame(bar)
        target.grid(row=1, column=1, columnspan=6, sticky="ew", pady=(8, 0), padx=6)
        self.mode_var = tk.StringVar(value="free")
        ttk.Radiobutton(target, text="Free an extra", variable=self.mode_var, value="free").pack(
            side="left"
        )
        ttk.Radiobutton(
            target, text="Reach total free space of", variable=self.mode_var, value="target"
        ).pack(side="left", padx=(10, 0))
        self.size_var = tk.StringVar(value="1GB")
        ttk.Entry(target, textvariable=self.size_var, width=10).pack(side="left", padx=6)
        ttk.Label(target, text="Move to drive").pack(side="left", padx=(14, 4))
        self.move_var = tk.StringVar()
        ttk.Entry(target, textvariable=self.move_var, width=16).pack(side="left")
        ttk.Button(target, text="…", width=3, command=self._choose_move).pack(side="left")

        options = ttk.Frame(target)
        options.pack(side="right")
        ttk.Label(options, text="Planner").pack(side="left")
        self.planner_var = tk.StringVar(value=PLANNER_LABELS["bnb"])
        ttk.Combobox(
            options,
            textvariable=self.planner_var,
            values=[PLANNER_LABELS[n] for n in PLANNER_NAMES],
            state="readonly",
            width=18,
        ).pack(side="left", padx=4)
        ttk.Label(options, text="Limit (s)").pack(side="left", padx=(6, 4))
        self.time_var = tk.StringVar(value="10")
        ttk.Spinbox(options, from_=1, to=600, textvariable=self.time_var, width=5).pack(side="left")
        self.plan_btn = ttk.Button(bar, text="Plan", style="Accent.TButton", command=self._plan)
        self.plan_btn.grid(row=1, column=7, sticky="ew", pady=(8, 0))

    def _build_status(self) -> None:
        bar = ttk.Frame(self, padding=(10, 2, 10, 6))
        bar.pack(fill="x")
        self.status_var = tk.StringVar(value="Choose a folder to scan, or open a saved dataset.")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self._cancel_task)
        self.progress = ttk.Progressbar(bar, length=220, mode="indeterminate")
        self.progress.pack(side="right")

    def _build_tabs(self) -> None:
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tabs.add(self._build_overview(), text="  Overview  ")
        self.tabs.add(self._build_files(), text="  Files & risk  ")
        self.tabs.add(self._build_plan(), text="  Plan review  ")
        self.tabs.add(self._build_results(), text="  Results  ")

    def _build_overview(self) -> ttk.Frame:
        tab = ttk.Frame(self.tabs, padding=10)
        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True)
        self.overview_var = tk.StringVar()
        ttk.Label(
            left, textvariable=self.overview_var, style="Summary.TLabel", justify="left"
        ).pack(anchor="w", pady=(0, 8))
        self.category_table = Table(
            left,
            [
                Column("category", "Category", 120),
                Column("files", "Files", 70, "e", _count),
                Column("total_bytes", "Size", 90, "e", format_bytes),
                Column("share", "Share", 70, "e", _share),
                Column("median_age_days", "Age (d, median)", 115, "e", _days),
                Column("duplicate_files", "Dup. copies", 85, "e", _count),
            ],
            height=14,
        )
        self.category_table.pack(fill="both", expand=True)
        self.chart = CategoryChart(tab)
        self.chart.pack(side="right", fill="both", expand=True, padx=(12, 0))
        return tab

    def _build_files(self) -> ttk.Frame:
        tab = ttk.Frame(self.tabs, padding=10)
        filters = ttk.Frame(tab)
        filters.pack(fill="x", pady=(0, 6))
        ttk.Label(filters, text="Category").pack(side="left")
        self.category_var = tk.StringVar(value="All")
        box = ttk.Combobox(
            filters,
            textvariable=self.category_var,
            values=["All", *[c.value for c in FileCategory]],
            state="readonly",
            width=14,
        )
        box.pack(side="left", padx=4)
        box.bind("<<ComboboxSelected>>", lambda _: self._refresh_files())
        self.protected_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filters, text="Protected only", variable=self.protected_var, command=self._refresh_files
        ).pack(side="left", padx=10)
        ttk.Label(filters, text="Path contains").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.search_var, width=30)
        search.pack(side="left", padx=4)
        search.bind("<Return>", lambda _: self._refresh_files())
        ttk.Button(filters, text="Filter", command=self._refresh_files).pack(side="left")
        self.files_count_var = tk.StringVar()
        ttk.Label(filters, textvariable=self.files_count_var, style="Muted.TLabel").pack(
            side="right"
        )
        self.files_table = Table(
            tab,
            [
                Column(
                    "path", "Path (relative to scanned folder)", 460, "w", self._display_path, True
                ),
                Column("category", "Category", 95),
                Column("size_bytes", "Size", 85, "e", format_bytes),
                Column("age_days", "Age (days)", 80, "e", _days),
                Column("risk", "Risk", 55, "e", _risk),
                Column("protection_reason", "Protected because", 150),
                Column("best_action", "Best action (h)", 105),
                Column("best_benefit", "Would free", 85, "e", format_bytes),
            ],
        )
        self.files_table.pack(fill="both", expand=True)
        return tab

    def _build_plan(self) -> ttk.Frame:
        tab = ttk.Frame(self.tabs, padding=10)
        self.plan_summary_var = tk.StringVar()
        ttk.Label(
            tab, textvariable=self.plan_summary_var, style="Summary.TLabel", justify="left",
            wraplength=1150,
        ).pack(anchor="w", pady=(0, 6))  # fmt: skip

        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=(0, 6))
        self.plan_buttons = [
            ttk.Button(buttons, text="Approve all", command=self._approve_all),
            ttk.Button(buttons, text="Approve none", command=self._approve_none),
            ttk.Button(buttons, text="Approve selected", command=lambda: self._approve_sel(True)),
            ttk.Button(
                buttons, text="Unapprove selected", command=lambda: self._approve_sel(False)
            ),
            ttk.Button(buttons, text="Reject selected & re-plan", command=self._reject_selected),
            ttk.Button(buttons, text="Clear rejections", command=self._clear_rejections),
            ttk.Button(buttons, text="Export plan…", command=self._export_plan),
        ]
        for button in self.plan_buttons:
            button.pack(side="left", padx=(0, 4))
        ttk.Label(
            buttons, text="Click ☑ or press Space to toggle approval", style="Muted.TLabel"
        ).pack(side="right")

        self.plan_table = Table(
            tab,
            [
                Column("approved", "OK", 40, "center", lambda v: CHECKED if v else UNCHECKED),
                Column("action", "Action", 95),
                Column("what", "What happens", 200),
                Column(
                    "path", "Path (relative to scanned folder)", 430, "w", self._display_path, True
                ),
                Column("size_bytes", "Size", 85, "e", format_bytes),
                Column("benefit_bytes", "Frees", 85, "e", format_bytes),
                Column("risk", "Risk", 55, "e", _risk),
                Column("cost", "Cost", 60, "e", lambda v: f"{float(v):.3f}"),
            ],
        )
        self.plan_table.pack(fill="both", expand=True)
        self.plan_table.tree.bind("<Button-1>", self._on_plan_click)
        self.plan_table.tree.bind("<space>", lambda _: self._toggle_selected())

        footer = ttk.Frame(tab)
        footer.pack(fill="x", pady=(8, 0))
        self.approval_var = tk.StringVar()
        ttk.Label(footer, textvariable=self.approval_var, style="Summary.TLabel").pack(side="left")
        self.execute_btn = ttk.Button(
            footer, text="Execute approved…", style="Accent.TButton",
            command=lambda: self._execute(dry_run=False),
        )  # fmt: skip
        self.execute_btn.pack(side="right")
        self.dry_run_btn = ttk.Button(
            footer, text="Dry run", command=lambda: self._execute(dry_run=True)
        )
        self.dry_run_btn.pack(side="right", padx=6)
        return tab

    def _build_results(self) -> ttk.Frame:
        tab = ttk.Frame(self.tabs, padding=10)
        self.results_var = tk.StringVar(value="Nothing has been executed yet.")
        ttk.Label(tab, textvariable=self.results_var, style="Summary.TLabel", justify="left").pack(
            anchor="w", pady=(0, 6)
        )
        self.results_table = Table(
            tab,
            [
                Column("status", "Status", 70),
                Column("action", "Action", 95),
                Column(
                    "path", "Path (relative to scanned folder)", 430, "w", self._display_path, True
                ),
                Column("bytes_freed", "Bytes freed", 90, "e", format_bytes),
                Column("message", "Details", 380),
            ],
        )
        self.results_table.pack(fill="both", expand=True)
        footer = ttk.Frame(tab)
        footer.pack(fill="x", pady=(6, 0))
        self.journal_var = tk.StringVar()
        # Button first, so a long journal path can never push it out of view.
        ttk.Button(footer, text="Open logs & journals", command=self._open_journal).pack(
            side="right"
        )
        ttk.Label(
            footer, textvariable=self.journal_var, style="Muted.TLabel", wraplength=1000
        ).pack(side="left", fill="x", expand=True)
        return tab

    # ================================================================== background tasks

    def _start(self, label: str, work: Callable[[], Any], done: Callable[[Any], None], *,
               determinate: bool = False, cancellable: bool = False) -> None:  # fmt: skip
        if self._busy:
            return
        self._busy = True
        self.status_var.set(label + "…")
        self.progress.configure(mode="determinate" if determinate else "indeterminate", value=0)
        if not determinate:
            self.progress.start(12)
        if cancellable:
            self.cancel_btn.pack(side="right", padx=6)
        self._refresh_buttons()

        def worker() -> None:
            try:
                result = work()
            except Exception as exc:  # reported in the UI thread
                self._queue.put(("error", label, exc))
            else:
                self._queue.put(("done", done, result))

        threading.Thread(target=worker, daemon=True).start()

    def _progress(self, text: str, percent: float | None = None) -> None:
        """Thread-safe progress update (called from the worker)."""
        self._queue.put(("progress", text, percent))

    def _poll(self) -> None:
        try:
            while True:
                kind, first, second = self._queue.get_nowait()
                if kind == "progress":
                    self.status_var.set(first)
                    if second is not None:
                        self.progress.configure(value=second)
                    continue
                self._busy = False
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)  # no leftover chunk
                self.cancel_btn.pack_forget()
                if kind == "error":
                    self.status_var.set(f"{first} failed.")
                    expected = isinstance(second, WorkflowError | ValueError | OSError)
                    detail = str(second) if expected else f"{type(second).__name__}: {second}"
                    messagebox.showerror("StorageOps", f"{first} failed:\n\n{detail}", parent=self)
                else:
                    first(second)
                self._refresh_buttons()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _cancel_task(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.status_var.set("Cancelling after the current file…")

    @property
    def busy(self) -> bool:
        return self._busy

    # ================================================================== actions

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Folder to analyse")
        if path:
            self.source_var.set(path)

    def _choose_dataset(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Dataset saved by `storageops scan -o`",
            filetypes=[("StorageOps dataset", "*.csv *.parquet"), ("All files", "*.*")],
        )
        if path:
            self.source_var.set(path)

    def _choose_move(self) -> None:
        path = filedialog.askdirectory(parent=self, title="MOVE destination (another drive)")
        if path:
            self.move_var.set(path)

    def _load(self) -> None:
        source = self.source_var.get().strip()
        if not source or not Path(source).exists():
            messagebox.showinfo("StorageOps", "Choose an existing folder or dataset.", parent=self)
            return
        move = self.move_var.get().strip() or None
        if Path(source).is_file():
            question = (
                f"Open this saved dataset?\n\n{source}\n\n"
                "StorageOps will only read the dataset. Nothing on disk is changed."
            )
        else:
            question = (
                f"Scan this folder?\n\n{source}\n\n"
                "StorageOps will read file names, sizes and dates, and read file contents "
                "to find duplicates. Nothing is changed, moved or deleted."
            )
        if not self._ask_ok("Scan / Load", question):
            return

        def work() -> None:
            self.session.load(
                source,
                user_ok=True,
                progress=lambda n, _d: self._progress(f"Scanning… {n:,} files"),
            )
            self._progress("Analysing risk and safety…")
            self.session.ensure_analysis(move)

        self._start(f"Loading {source}", work, lambda _: self._after_load())

    def _after_load(self) -> None:
        snapshot = self.session.snapshot
        self.status_var.set(
            f"Loaded {len(snapshot.files):,} files from {snapshot.root}. "
            "Set a target and press Plan."
        )
        self._refresh_all()
        self.tabs.select(0)

    def _plan(self) -> None:
        if self.session.snapshot is None:
            messagebox.showinfo("StorageOps", "Scan a folder or open a dataset first.", parent=self)
            return
        try:
            size = parse_size(self.size_var.get())
            time_limit = float(self.time_var.get())
        except ValueError as exc:
            messagebox.showerror("StorageOps", str(exc), parent=self)
            return
        planner = next(k for k, v in PLANNER_LABELS.items() if v == self.planner_var.get())
        mode = self.mode_var.get()
        move = self.move_var.get().strip() or None
        target = f"free an extra {format_bytes(size)}" if mode == "free" else (
            f"reach {format_bytes(size)} of free space")  # fmt: skip
        question = (
            f"Compute a cleanup plan?\n\nTarget: {target}\n"
            f"Planner: {self.planner_var.get()} (up to {time_limit:g} s)\n"
            f"MOVE destination: {move or 'none'}\n\n"
            "This only calculates a proposal. Nothing on disk is changed, and every "
            "proposed action still needs your approval."
        )  # fmt: skip
        if not self._ask_ok("Plan", question):
            return

        def work() -> Any:
            return self.session.plan(
                size, mode=mode, planner=planner, time_limit_s=time_limit,
                move_destination=move, user_ok=True,
            )  # fmt: skip

        self._start("Planning", work, lambda _: self._after_plan())

    def _after_plan(self) -> None:
        result = self.session.result
        self.status_var.set(
            f"Plan ready: {result.status}, {len(result.plan.actions):,} proposed actions. "
            "Review and approve them in the Plan review tab."
        )
        self._refresh_files()
        self._refresh_plan()
        self.tabs.select(2)

    def _on_plan_click(self, event: tk.Event) -> str | None:
        tree = self.plan_table.tree
        if (
            tree.identify_region(event.x, event.y) != "cell"
            or tree.identify_column(event.x) != "#1"
        ):
            return None
        iid = tree.identify_row(event.y)
        if iid:
            self._set_approval([iid], iid not in self.session.approved)
        return "break"

    def _toggle_selected(self) -> None:
        selected = self.plan_table.selected()
        if selected:
            self._set_approval(selected, not all(p in self.session.approved for p in selected))

    def _set_approval(self, paths: list[str], approved: bool) -> None:
        if self._busy or self.session.needs_rescan:
            return
        self.session.set_approved(paths, approved)
        for path in paths:
            self.plan_table.update_row(path, approved=path in self.session.approved)
        self._refresh_approval()

    def _approve_all(self) -> None:
        self._set_approval([a.path for a in self.session.proposed], True)

    def _approve_none(self) -> None:
        self._set_approval([a.path for a in self.session.proposed], False)

    def _approve_sel(self, approved: bool) -> None:
        self._set_approval(self.plan_table.selected(), approved)

    def _reject_selected(self) -> None:
        selected = self.plan_table.selected()
        if not selected:
            messagebox.showinfo("StorageOps", "Select the rows to reject first.", parent=self)
            return
        question = (
            f"Keep {len(selected):,} selected file(s) and compute a new plan without them?"
            "\n\nThis only calculates a proposal. Nothing on disk is changed."
        )
        if not self._ask_ok("Reject & re-plan", question):
            return
        self._start(
            f"Re-planning without {len(selected)} rejected file(s)",
            lambda: self.session.replan_without(selected, user_ok=True),
            lambda _: self._after_plan(),
        )

    def _clear_rejections(self) -> None:
        self.session.clear_rejections()
        self._refresh_plan()

    def _export_plan(self) -> None:
        if self.session.result is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="storageops_plan.csv",
        )  # fmt: skip
        if path:
            frame = plan_to_frame(self.session.result.plan)
            frame.insert(0, "approved", frame["path"].isin(self.session.approved))
            frame.to_csv(path, index=False)
            self.status_var.set(f"Plan exported to {path}")

    def _execute(self, *, dry_run: bool) -> None:
        summary = self.session.approval_summary()
        if not summary.actions:
            messagebox.showinfo("StorageOps", "Approve at least one action first.", parent=self)
            return
        if dry_run:
            question = (
                f"Dry run {summary.actions:,} approved action(s)?\n\n"
                "Each file is checked (still there, unchanged since the scan, not "
                "protected). Nothing is changed. The check is recorded in a journal in "
                f"{self.session.journal_dir}."
            )
            if not self._ask_ok("Dry run", question):
                return
        elif not self._confirm_execution(summary):
            return
        self._cancel = threading.Event()
        verb = "Checking" if dry_run else "Executing"

        def work() -> Any:
            return self.session.execute(
                dry_run=dry_run,
                user_ok=True,
                cancel=self._cancel,
                progress=lambda d, t, p: self._progress(
                    f"{verb} {d}/{t}: {Path(p).name}", 100 * d / max(t, 1)
                ),
            )

        self._start("Dry run" if dry_run else "Execution", work, self._after_execute,
                    determinate=True, cancellable=True)  # fmt: skip

    def _confirm_execution(self, summary: Any) -> bool:
        lines = [
            f"  {count:,} × {action}: {describe_action(Action(action))}"
            for action, count in sorted(summary.by_action.items())
        ]
        message = (
            "This will change files on disk:\n\n" + "\n".join(lines) +
            f"\n\nExpected to free about {format_bytes(summary.bytes)}.\n\n"
            "Deleted files go to the Recycle Bin. ARCHIVE and MOVE remove an original only "
            "after verifying its copy. Every operation is recorded in the journal.\n\nContinue?"
        )  # fmt: skip
        answer = messagebox.askyesno(
            "Execute approved actions", message, icon="warning", default="no", parent=self
        )
        log.info("execution dialog: user answered %s", "YES" if answer else "no")
        return answer

    def _ask_ok(self, title: str, question: str) -> bool:
        """Every step that does work goes through here: nothing runs without OK."""
        answer = messagebox.askokcancel(title, question, parent=self)
        log.info("%s dialog: user answered %s", title, "OK" if answer else "Cancel")
        if not answer:
            self.status_var.set(f"{title} cancelled. Nothing was done.")
        return bool(answer)

    def _after_execute(self, report: Any) -> None:
        counts = report.counts()
        rows = [
            {
                "status": r.status, "action": r.action.value, "path": r.path,
                "bytes_freed": r.bytes_freed, "message": r.message,
            }
            for r in report.results
        ]  # fmt: skip
        self.results_table.set_rows(rows, key="path")
        names = {"dry-run": "checked OK", "done": "done", "skipped": "skipped",
                 "failed": "failed"}  # fmt: skip
        parts = ", ".join(f"{n:,} {names.get(status, status)}" for status, n in counts.items())
        if report.dry_run:
            text = (
                f"Dry run ({parts}). Nothing was changed. Would free "
                f"{format_bytes(report.bytes_freed)}."
            )
            if counts.get("skipped"):
                text += " Skipped files will be skipped again; see Details."
            if self.session.can_execute:
                text += " You can now execute this selection from the Plan review tab."
        else:
            text = (
                f"Executed: {parts}. Freed {format_bytes(report.bytes_freed)}. "
                "Re-scan before planning again."
            )
        if report.cancelled:
            text += " (Cancelled before finishing.)"
        self.results_var.set(text)
        self.journal_var.set(f"Journal: {report.journal_path}    Log: {log_file()}")
        self.status_var.set(text)
        self._refresh_plan()
        self.tabs.select(3)

    def _open_journal(self) -> None:
        folder = self.session.journal_dir.parent  # StorageOps home: logs/ and journal/
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opening a folder in Explorer
        else:
            messagebox.showinfo("StorageOps", f"Journal folder:\n{folder}", parent=self)

    # ================================================================== refresh

    def _display_path(self, path: str) -> str:
        """Show paths relative to the scanned folder; rows keep their full path."""
        snapshot = self.session.snapshot
        if snapshot is not None:
            root = snapshot.root.rstrip("\\/")
            if path.casefold().startswith(root.casefold()) and len(path) > len(root):
                return path[len(root) + 1 :]
        return path

    def _refresh_all(self) -> None:
        self._refresh_overview()
        self._refresh_files()
        self._refresh_plan()
        self._refresh_buttons()

    def _refresh_overview(self) -> None:
        snapshot = self.session.snapshot
        if snapshot is None:
            self.overview_var.set("No storage environment loaded yet.")
            self.category_table.set_rows([], key="category")
            self.chart.show(category_summary(records_to_frame([])))
            return
        disk, stats, dup = snapshot.disk, snapshot.scan_stats, snapshot.duplicates
        lines = [
            f"Root: {snapshot.root}",
            f"Volume {disk.volume}  {format_bytes(disk.free_bytes)} free of "
            f"{format_bytes(disk.total_bytes)} ({disk.free_ratio:.1%})",
            f"{stats.files_found:,} files, {format_bytes(snapshot.total_bytes)}, "
            f"in {stats.dirs_visited:,} folders",
        ]
        if dup is not None:
            lines.append(
                f"Duplicates: {dup.groups:,} groups, "
                f"{format_bytes(dup.reclaimable_bytes)} redundant"
            )
        analysis = self.session.analysis
        if analysis is not None:
            protected = int(analysis.protection["protected"].sum())
            lines.append(
                f"Protected: {protected:,} files   Cleanup candidates: "
                f"{int(analysis.candidates.sum()):,} files"
            )
        if snapshot.issues:
            lines.append(f"Unreadable paths: {len(snapshot.issues):,}")
        self.overview_var.set("\n".join(lines))
        summary = category_summary(snapshot.files)
        self.category_table.set_rows(summary.to_dict("records"), key="category")
        self.chart.show(summary)

    def _refresh_files(self) -> None:
        analysis = self.session.analysis
        if analysis is None:
            self.files_table.set_rows([], key="path")
            self.files_count_var.set("")
            return
        table = analysis.to_frame()
        if self.category_var.get() != "All":
            table = table[table["category"].astype(str) == self.category_var.get()]
        if self.protected_var.get():
            table = table[table["protected"]]
        needle = self.search_var.get().strip().casefold()
        if needle:
            table = table[table["path"].str.casefold().str.contains(needle, regex=False)]
        shown = table.nlargest(FILES_SHOWN, "size_bytes")
        columns = ["path", "category", "size_bytes", "age_days", "risk", "protection_reason",
                   "best_action", "best_benefit"]  # fmt: skip
        rows = shown[columns].astype({"category": str, "protection_reason": str}).to_dict("records")
        for row in rows:
            if row["best_action"] == "KEEP":
                row["best_benefit"] = None
        self.files_table.set_rows(rows, key="path")
        self.files_count_var.set(
            f"Showing {len(shown):,} of {len(table):,} matching files (largest first)"
        )

    def _refresh_plan(self) -> None:
        session, result = self.session, self.session.result
        if result is None:
            self.plan_summary_var.set("No plan yet. Load a source, set a target and press Plan.")
            self.plan_table.set_rows([], key="path")
            self._refresh_approval()
            return
        plan, stats, problem = result.plan, result.stats, session.problem
        planner = PLANNER_LABELS.get(self.planner_name_for(result.planner), result.planner)
        text = f"{planner}: {result.status}. Must free {format_bytes(problem.required_bytes)}. "
        if result.status == "infeasible" or (not plan.is_goal and not plan.actions):
            text += (
                "The target cannot be reached with safe actions: at most "
                f"{format_bytes(problem.max_freeable_bytes)} can be freed. Lower the target, "
                "add a MOVE destination, or adjust the safety settings."
            )
        elif not plan.actions:
            text += "The target is already met; nothing needs to change."
        else:
            gap = "" if result.gap != result.gap else f", optimality gap {result.gap:.2%}"
            text += (
                f"Proposed: {len(plan.actions):,} actions freeing {format_bytes(plan.freed_bytes)}"
                f" (total cost {plan.total_cost:.3f}{gap}). Searched "
                f"{stats.nodes_generated:,} nodes in {stats.elapsed_s:.2f}s."
            )
            if not plan.meets_target:
                text += " ⚠ This plan does not reach the target."
        if session.rejected:
            text += f"  Rejected files kept: {len(session.rejected):,}."
        self.plan_summary_var.set(text)
        rows = session.plan_table().to_dict("records")
        for row in rows:
            row["what"] = describe_action(Action(row["action"]))
        self.plan_table.set_rows(rows, key="path")
        self._refresh_approval()

    @staticmethod
    def planner_name_for(planner_attr: str) -> str:
        return {"branch_and_bound": "bnb", "greedy": "greedy",
                "greedy_heuristic": "greedy-heuristic"}.get(planner_attr, planner_attr)  # fmt: skip

    def _refresh_approval(self) -> None:
        session = self.session
        if session.result is None:
            self.approval_var.set("")
        elif session.needs_rescan:
            self.approval_var.set(
                "Files were changed. Re-scan (Scan / Load) before planning again."
            )
        else:
            s = session.approval_summary()
            text = (
                f"Approved {s.actions:,} of {len(session.proposed):,} actions: frees "
                f"{format_bytes(s.bytes)} of {format_bytes(s.required)} required."
            )
            if s.actions and s.shortfall:
                text += f"  ⚠ {format_bytes(s.shortfall)} short of the target."
            if s.actions:
                text += (
                    "  Dry run done: ready to execute." if session.can_execute
                    else "  Run a dry run before executing."
                )  # fmt: skip
            self.approval_var.set(text)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        session = self.session
        idle = not self._busy
        has_plan = session.result is not None and not session.needs_rescan
        self.load_btn.state(["!disabled"] if idle else ["disabled"])
        can_plan = idle and session.snapshot is not None and not session.needs_rescan
        self.plan_btn.state(["!disabled"] if can_plan else ["disabled"])
        for button in self.plan_buttons:
            button.state(["!disabled"] if idle and has_plan else ["disabled"])
        self.dry_run_btn.state(
            ["!disabled"] if idle and has_plan and session.approved else ["disabled"]
        )
        self.execute_btn.state(["!disabled"] if idle and session.can_execute else ["disabled"])


def main(source: str | None = None, config_path: str | Path | None = None) -> None:
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    setup_logging()
    log.info("GUI started (source=%s)", source)
    app = StorageOpsApp(Session(load_config(config_path)), source=source)
    try:
        app.mainloop()
    finally:
        log.info("GUI closed")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
