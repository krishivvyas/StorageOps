"""Reusable tkinter widgets: a sortable table and the category bar chart."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from storageops.formatting import format_bytes

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = "#2a78d6"


@dataclass(frozen=True)
class Column:
    key: str
    heading: str
    width: int = 100
    anchor: str = "w"
    fmt: Callable[[Any], str] = str
    stretch: bool = False


class Table(ttk.Frame):
    """A Treeview with scrollbars and click-to-sort headings.

    Rows are dicts. Raw values are kept for sorting (sizes sort as numbers,
    not as "1.5 MB" text), while the cells show the formatted text.
    """

    def __init__(self, parent: tk.Misc, columns: Sequence[Column], **kwargs: Any) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.tree = ttk.Treeview(self, columns=[c.key for c in columns], show="headings", **kwargs)
        for col in columns:
            self.tree.heading(col.key, text=col.heading, command=lambda k=col.key: self.sort(k))
            self.tree.column(col.key, width=col.width, anchor=col.anchor, stretch=col.stretch)
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._rows: dict[str, dict[str, Any]] = {}
        self._sort_key: str | None = None
        self._descending = False

    def set_rows(self, rows: Sequence[dict[str, Any]], key: str) -> None:
        """Replace all rows; ``key`` names the field used as each row's id."""
        self.tree.delete(*self.tree.get_children())
        self._rows = {}
        for row in rows:
            iid = str(row[key])
            self._rows[iid] = row
            self.tree.insert("", "end", iid=iid, values=self._cells(row))

    def update_row(self, iid: str, **changes: Any) -> None:
        self._rows[iid].update(changes)
        self.tree.item(iid, values=self._cells(self._rows[iid]))

    def row(self, iid: str) -> dict[str, Any]:
        return self._rows[iid]

    def selected(self) -> list[str]:
        return list(self.tree.selection())

    def sort(self, key: str) -> None:
        self._descending = not self._descending if self._sort_key == key else key != "path"
        self._sort_key = key
        order = sorted(
            self._rows,
            key=lambda iid: _sortable(self._rows[iid].get(key)),
            reverse=self._descending,
        )
        for index, iid in enumerate(order):
            self.tree.move(iid, "", index)
        for col in self.columns:
            arrow = (" ▼" if self._descending else " ▲") if col.key == key else ""
            self.tree.heading(col.key, text=col.heading + arrow)

    def _cells(self, row: dict[str, Any]) -> list[str]:
        cells = []
        for col in self.columns:
            value = row.get(col.key)
            missing = value is None or (isinstance(value, float) and value != value)
            cells.append("" if missing else col.fmt(value))
        return cells


def _sortable(value: Any) -> tuple[int, Any]:
    if value is None or (isinstance(value, float) and value != value):
        return (1, 0)
    if isinstance(value, bool | int | float):
        return (0, float(value))
    return (0, str(value).casefold())


class CategoryChart(ttk.Frame):
    """Horizontal bars of bytes per category: one hue, sorted, with a hover tooltip.

    The category table next to it is the table view of the same numbers.
    """

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(5, 4), dpi=96, facecolor=SURFACE)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self._bars: list = []
        self._data = pd.DataFrame()
        self._tooltip = None
        self.canvas.mpl_connect("motion_notify_event", self._on_hover)
        self.show(pd.DataFrame())

    def show(self, summary: pd.DataFrame) -> None:
        """``summary``: category_summary() output (category, files, total_bytes, share)."""
        ax = self.ax
        ax.clear()
        ax.set_facecolor(SURFACE)
        self._data = (
            summary.sort_values("total_bytes").reset_index(drop=True) if len(summary) else summary
        )
        if summary.empty:
            ax.text(0.5, 0.5, "Scan a folder to see where the space goes",
                    ha="center", va="center", color=MUTED, transform=ax.transAxes)  # fmt: skip
            ax.set_axis_off()
            self.canvas.draw_idle()
            return
        ax.set_axis_on()
        data = self._data
        heights = data["total_bytes"].to_numpy(dtype=float)
        self._bars = list(ax.barh(range(len(data)), heights, height=0.6, color=SERIES,
                                  edgecolor=SURFACE, linewidth=2))  # fmt: skip
        ax.set_yticks(range(len(data)), [c.replace("_", " ") for c in data["category"]])
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(AXIS)
        ax.spines["bottom"].set_color(AXIS)
        ax.tick_params(colors=MUTED, labelcolor=INK_SECONDARY, labelsize=8)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.xaxis.set_major_formatter(lambda v, _: format_bytes(v) if v else "0")
        # selective direct labels: the three largest categories only
        for index in range(max(0, len(data) - 3), len(data)):
            ax.annotate(f" {format_bytes(heights[index])}", (heights[index], index),
                        va="center", fontsize=8, color=INK_SECONDARY)  # fmt: skip
        ax.set_xlim(0, heights.max() * 1.25)
        ax.set_title("Space by category", loc="left", fontsize=10, fontweight="bold", color=INK)
        self._tooltip = ax.annotate(
            "", (0, 0), xytext=(10, 10), textcoords="offset points", fontsize=8, color=INK,
            bbox={"boxstyle": "round,pad=0.4", "fc": "white", "ec": AXIS},
            visible=False,
        )  # fmt: skip
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _on_hover(self, event: Any) -> None:
        if self._tooltip is None or event.inaxes is not self.ax:
            return
        for index, bar in enumerate(self._bars):
            if bar.contains(event)[0] or (
                event.ydata is not None and abs(event.ydata - index) < 0.45  # generous hit area
            ):
                row = self._data.iloc[index]
                self._tooltip.xy = (bar.get_width(), index)
                self._tooltip.set_text(
                    f"{row['category'].replace('_', ' ')}\n{format_bytes(row['total_bytes'])}"
                    f" in {int(row['files']):,} files ({row['share']:.1%})"
                )
                self._tooltip.set_visible(True)
                self.canvas.draw_idle()
                return
        if self._tooltip.get_visible():
            self._tooltip.set_visible(False)
            self.canvas.draw_idle()
