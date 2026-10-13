"""Matplotlib figures for the benchmark report (static PNGs for the written report).

Visual rules:

* each planner has a fixed colour (validated categorical slots, assigned by
  entity rather than rank) and its own marker shape, so identity never relies on
  colour alone; three slots are below 3:1 contrast, which is why every figure
  also has a legend, and the report ships the same numbers as tables;
* one y-axis per chart; measures of different scale go in separate figures,
  and the sensitivity study uses small multiples;
* recessive chrome: hairline solid y-grid, muted axes, text in ink colours
  (never in a series colour).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # file output only; no display needed

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

PLANNER_STYLE: dict[str, tuple[str, str, str]] = {  # label, colour, marker
    "greedy": ("Greedy (ratio)", "#2a78d6", "o"),
    "greedy_heuristic": ("Greedy (heuristic)", "#eb6834", "s"),
    "bnb": ("Branch & bound", "#1baf7a", "^"),
    "bnb_heuristic": ("Branch & bound (h-order)", "#eda100", "D"),
    "exhaustive": ("Exhaustive", "#e87ba4", "v"),
}
WEIGHT_LABELS = {
    "weight_benefit": "Benefit weight",
    "weight_inactivity": "Inactivity weight",
    "weight_duplicate": "Duplicate weight",
    "weight_risk": "Risk weight",
}
SERIES = "#2a78d6"  # single-series figures use slot 1

plt.rcParams.update(
    {
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
        "font.size": 10,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_SECONDARY,
        "axes.titlecolor": INK,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": INK_SECONDARY,
        "ytick.labelcolor": INK_SECONDARY,
        "legend.frameon": False,
        "legend.labelcolor": INK_SECONDARY,
        "lines.linewidth": 2,
        "lines.markersize": 6,
    }
)


def _style_axes(ax: plt.Axes, *, log_x: bool = False, log_y: bool = False) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(AXIS)
    ax.spines["bottom"].set_color(AXIS)
    ax.grid(axis="y", color=GRID, linewidth=0.8, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(length=3, width=0.8)
    if log_x:
        ax.set_xscale("log", base=2)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    if log_y:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(LogLocator(base=10, numticks=12))
        ax.yaxis.set_minor_formatter(NullFormatter())


def _planner_lines(ax: plt.Axes, table: pd.DataFrame, value: str) -> None:
    """One line per planner present: median ``value`` against n_files."""
    for planner, (label, colour, marker) in PLANNER_STYLE.items():
        rows = table[table["planner"] == planner]
        if rows.empty:
            continue
        series = rows.groupby("n_files")[value].median()
        ax.plot(series.index, series.to_numpy(), color=colour, marker=marker, label=label,
                markeredgecolor=SURFACE, markeredgewidth=1.5)  # fmt: skip
        if len(series) == 1:  # a lone point (exhaustive search) needs a direct label
            ax.annotate(f"{label} ({series.index[0]:,} files only)",
                        (series.index[0], series.iloc[0]), xytext=(8, -3),
                        textcoords="offset points", fontsize=8, color=INK_SECONDARY)  # fmt: skip


def _legend_below(ax: plt.Axes) -> None:
    handles, _ = ax.get_legend_handles_labels()
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.16), ncol=min(len(handles), 3),
              handlelength=1.6, columnspacing=1.4)  # fmt: skip


def _save(fig: plt.Figure, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_scaling_time(scaling: pd.DataFrame, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    data = scaling.assign(elapsed_ms=scaling["elapsed_s"].clip(lower=1e-5) * 1000)
    _planner_lines(ax, data, "elapsed_ms")
    _style_axes(ax, log_x=True, log_y=True)
    ax.set_title("Planning time grows with instance size")
    ax.set_xlabel("Files in instance (log scale)")
    ax.set_ylabel("Median planning time, ms (log scale)")
    _legend_below(ax)
    return _save(fig, path)


def plot_scaling_nodes(scaling: pd.DataFrame, path: Path) -> Path:
    searchers = scaling[scaling["planner"].isin(["bnb", "bnb_heuristic", "exhaustive"])]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    _planner_lines(ax, searchers.assign(nodes=searchers["nodes_generated"].clip(lower=1)), "nodes")
    _style_axes(ax, log_x=True, log_y=True)
    ax.set_title("Search effort: nodes generated")
    ax.set_xlabel("Files in instance (log scale)")
    ax.set_ylabel("Median nodes generated (log scale)")
    _legend_below(ax)
    fig.text(0.01, 0.01, "Runs proved optimal at the root (0 nodes) are plotted at 1.",
             fontsize=8, color=MUTED)  # fmt: skip
    return _save(fig, path)


def plot_quality(results: pd.DataFrame, path: Path) -> Path:
    """Mean excess cost over the best plan found, per size class and planner."""
    planners = [p for p in PLANNER_STYLE if p != "exhaustive" and p in set(results["planner"])]
    classes = [c for c in ("small", "medium", "large") if c in set(results["size_class"])]
    table = (
        results[results["planner"].isin(planners)]
        .groupby(["size_class", "planner"])["excess_cost"]
        .mean()
        .mul(100)
    )
    fig, ax = plt.subplots(figsize=(7, 4.2))
    width = min(0.8 / max(len(planners), 1), 0.2)
    for k, planner in enumerate(planners):
        label, colour, _ = PLANNER_STYLE[planner]
        xs = [i + (k - (len(planners) - 1) / 2) * width for i in range(len(classes))]
        ys = [table.get((c, planner), float("nan")) for c in classes]
        bars = ax.bar(xs, ys, width=width, color=colour, label=label,
                      edgecolor=SURFACE, linewidth=2)  # fmt: skip
        ax.bar_label(bars, labels=[f"{y:.1f}%" if y == y else "" for y in ys],
                     padding=2, fontsize=8, color=INK_SECONDARY)  # fmt: skip
    _style_axes(ax)
    ax.set_xticks(range(len(classes)), [c.capitalize() for c in classes])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_ylim(bottom=0)
    ax.set_title("Solution quality: extra cost over the best plan found")
    ax.set_ylabel("Mean excess cost")
    _legend_below(ax)
    return _save(fig, path)


def plot_sensitivity(
    sensitivity: pd.DataFrame,
    value: str,
    ylabel: str,
    title: str,
    path: Path,
    *,
    percent: bool = False,
    log_y: bool = False,
) -> Path:
    """2x2 small multiples: one panel per heuristic weight, one series each."""
    fig, axes = plt.subplots(2, 2, figsize=(8, 5.4), sharey=True)
    for ax, weight in zip(axes.flat, WEIGHT_LABELS, strict=True):
        rows = sensitivity[sensitivity["weight"] == weight]
        series = rows.groupby("value")[value].mean() * (100 if percent else 1)
        ax.plot(series.index, series.to_numpy(), color=SERIES, marker="o",
                markeredgecolor=SURFACE, markeredgewidth=1.5)  # fmt: skip
        default = rows.loc[rows["is_default"], "value"]
        if len(default):
            ax.axvline(default.iloc[0], color=AXIS, linewidth=1)
            ax.annotate("default", (default.iloc[0], 1), xycoords=("data", "axes fraction"),
                        xytext=(3, -10), textcoords="offset points", fontsize=8,
                        color=MUTED)  # fmt: skip
        _style_axes(ax, log_y=log_y)
        ax.set_title(WEIGHT_LABELS[weight], fontsize=10)
        if percent:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    for ax in axes[:, 0]:
        ax.set_ylabel(ylabel)
    for ax in axes[1, :]:
        ax.set_xlabel("Weight value (others at default)")
    fig.suptitle(title, x=0.02, ha="left", fontsize=11, fontweight="bold", color=INK)
    return _save(fig, path)
