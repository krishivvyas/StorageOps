"""Run the complete experiment and write CSVs, figures and a Markdown summary."""

from __future__ import annotations

import math
import numbers
import platform
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from storageops.benchmark import plots
from storageops.benchmark.profiles import SIZE_CLASSES, benchmark_suite, scaling_suite
from storageops.benchmark.runner import default_planners, run_benchmark, summarize
from storageops.benchmark.sensitivity import weight_sensitivity
from storageops.config import Config


@dataclass(frozen=True)
class ExperimentSettings:
    size_classes: Sequence[str] = tuple(SIZE_CLASSES)
    seeds: int = 3
    time_limit_s: float = 5.0
    scaling_sizes: Sequence[int] = (10, 20, 40, 80, 160, 320, 640, 1280, 2560)
    sensitivity_instances: int = 6
    sensitivity_values: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0)
    sensitivity_time_limit_s: float = 2.0
    run_scaling: bool = True
    run_sensitivity: bool = True

    @classmethod
    def quick(cls) -> ExperimentSettings:
        """A smoke-test configuration that finishes in well under a minute."""
        return cls(
            size_classes=("small", "medium"),
            seeds=1,
            time_limit_s=1.0,
            scaling_sizes=(10, 40, 160),
            sensitivity_instances=1,
            sensitivity_values=(0.0, 0.5, 1.0),
        )


Log = Callable[[str], None]


def run_experiment(
    out_dir: Path,
    settings: ExperimentSettings | None = None,
    *,
    config: Config | None = None,
    log: Log = print,
) -> dict[str, Path]:
    settings = settings or ExperimentSettings()
    out_dir = Path(out_dir)
    figures = out_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    started = time.perf_counter()

    def progress(stage: str) -> Callable[[int, int, str], None]:
        def report(done: int, total: int, name: str) -> None:
            if done == total or done % max(1, total // 10) == 0:
                log(f"  {stage}: {done}/{total} instances")

        return report

    planners = default_planners(settings.time_limit_s)

    log("Main benchmark (size class x duplicates x risk x target x seed)...")
    specs = benchmark_suite(settings.size_classes, seeds=range(settings.seeds))
    results = run_benchmark(specs, planners, config=config, progress=progress("benchmark"))
    written["results"] = _csv(results, out_dir / "results.csv")
    written["fig_quality"] = plots.plot_quality(results, figures / "quality_by_size.png")

    scaling = pd.DataFrame()
    if settings.run_scaling:
        log("Scaling study (growing file counts)...")
        specs = scaling_suite(settings.scaling_sizes, seeds=range(settings.seeds))
        scaling = run_benchmark(specs, planners, config=config, progress=progress("scaling"))
        written["scaling"] = _csv(scaling, out_dir / "scaling.csv")
        written["fig_time"] = plots.plot_scaling_time(scaling, figures / "scaling_time.png")
        written["fig_nodes"] = plots.plot_scaling_nodes(scaling, figures / "scaling_nodes.png")

    sensitivity = pd.DataFrame()
    if settings.run_sensitivity:
        log("Heuristic weight sensitivity...")
        specs = benchmark_suite(
            ["medium"],
            seeds=range(settings.sensitivity_instances),
            duplicate_ratios=(0.3,),
            risk_levels=("mixed",),
            target_fractions=(0.2,),
        )
        sensitivity = weight_sensitivity(
            specs,
            settings.sensitivity_values,
            config=config,
            time_limit_s=min(settings.sensitivity_time_limit_s, settings.time_limit_s),
            progress=progress("sensitivity"),
        )
        written["sensitivity"] = _csv(sensitivity, out_dir / "sensitivity.csv")
        if not sensitivity.empty:
            written["fig_sens_quality"] = plots.plot_sensitivity(
                sensitivity, "greedy_excess_cost", "Mean excess cost",
                "Heuristic weights vs. greedy (heuristic) plan quality",
                figures / "sensitivity_quality.png", percent=True,
            )  # fmt: skip
            written["fig_sens_nodes"] = plots.plot_sensitivity(
                sensitivity, "bnb_nodes", "Mean nodes generated",
                "Heuristic weights vs. branch & bound (h-order) search effort",
                figures / "sensitivity_nodes.png", log_y=True,
            )  # fmt: skip

    elapsed = time.perf_counter() - started
    summary = render_summary(results, scaling, sensitivity, settings, elapsed)
    written["summary"] = out_dir / "summary.md"
    written["summary"].write_text(summary, encoding="utf-8")
    log(f"Done in {elapsed:.0f}s. Summary: {written['summary']}")
    return written


def _csv(frame: pd.DataFrame, path: Path) -> Path:
    frame.to_csv(path, index=False)
    return path


# --------------------------------------------------------------------------- summary


def render_summary(
    results: pd.DataFrame,
    scaling: pd.DataFrame,
    sensitivity: pd.DataFrame,
    settings: ExperimentSettings,
    elapsed_s: float,
) -> str:
    lines = [
        "# StorageOps benchmark results",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} on {platform.platform()} "
        f"(Python {platform.python_version()}) in {elapsed_s:.0f}s.",
        f"Branch-and-bound time limit: {settings.time_limit_s:g}s per instance. "
        f"Seeds per configuration: {settings.seeds}.",
        "",
        "Quality is measured as excess cost over the cheapest goal plan any planner found on "
        "the same instance; that is the true optimum wherever branch-and-bound or exhaustive "
        "search finished (see `optimal_rate`).",
        "",
        "Figures: `figures/quality_by_size.png`, `figures/scaling_time.png`, "
        "`figures/scaling_nodes.png`, `figures/sensitivity_quality.png`, "
        "`figures/sensitivity_nodes.png`. Raw data: `results.csv`, `scaling.csv`, "
        "`sensitivity.csv`.",
        "",
    ]
    if not results.empty:
        lines += _section("By size class", summarize(results, "size_class"))
        lines += _section("By risk level", summarize(results, "risk_level"))
        lines += _section("By duplicate ratio", summarize(results, "duplicate_ratio"))
        lines += _section("By target fraction", summarize(results, "target_fraction"))
        lines += _instance_outcomes(results)
    if not scaling.empty:
        table = scaling.pivot_table(
            index="n_files", columns="planner", values="elapsed_s", aggfunc="median"
        ).mul(1000)
        lines += _section("Scaling: median planning time (ms)", table.reset_index())
        table = scaling.pivot_table(
            index="n_files", columns="planner", values="nodes_generated", aggfunc="median"
        )
        lines += _section("Scaling: median nodes generated", table.reset_index())
        hit = scaling[scaling["planner"] == "bnb"].groupby("n_files")["limit_reached"].mean()
        lines += _section("Scaling: share of runs where branch-and-bound hit the time limit",
                          hit.rename("limit_rate").reset_index())  # fmt: skip
    if not sensitivity.empty:
        table = sensitivity.pivot_table(
            index="value", columns="weight", values="greedy_excess_cost", aggfunc="mean"
        )
        lines += _section("Sensitivity: greedy (heuristic) mean excess cost", table.reset_index())
        table = sensitivity.pivot_table(
            index="value", columns="weight", values="bnb_nodes", aggfunc="mean"
        )
        lines += _section("Sensitivity: branch & bound (h-order) mean nodes", table.reset_index())
    return "\n".join(lines) + "\n"


def _instance_outcomes(results: pd.DataFrame) -> list[str]:
    per_instance = results.groupby("instance").agg(
        size_class=("size_class", "first"),
        risk_level=("risk_level", "first"),
        target_fraction=("target_fraction", "first"),
        feasible=("is_goal", "any"),
        proven_infeasible=("status", lambda s: (s == "infeasible").any()),
    )
    table = (
        per_instance.groupby(["size_class", "risk_level", "target_fraction"])
        .agg(
            instances=("feasible", "size"),
            feasible=("feasible", "sum"),
            proven_infeasible=("proven_infeasible", "sum"),
        )
        .reset_index()
    )
    return _section("Instance outcomes (failure analysis)", table)


def _section(title: str, frame: pd.DataFrame) -> list[str]:
    return [f"## {title}", "", markdown_table(frame), ""]


def markdown_table(frame: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table (no extra dependency)."""
    columns = [str(c) for c in frame.columns]

    def fmt(value: object) -> str:
        if isinstance(value, bool | np.bool_):
            return "yes" if value else "no"
        if isinstance(value, numbers.Integral):
            return f"{int(value):,}"
        if isinstance(value, numbers.Real):
            value = float(value)
            if math.isnan(value):
                return "-"
            if value != 0 and (abs(value) >= 1e5 or abs(value) < 1e-3):
                return f"{value:.3g}"
            return f"{value:,.3f}".rstrip("0").rstrip(".")
        return str(value)

    rows = [[fmt(v) for v in row] for row in frame.itertuples(index=False)]
    out = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(out)
