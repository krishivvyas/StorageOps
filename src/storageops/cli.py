"""Command-line entry point: ``storageops <command>``.

Commands by phase: ``scan`` (1), ``analyze`` (2), ``plan`` (3), ``bench`` (4) and ``gui`` (5).
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pandas as pd

from storageops import __version__
from storageops.activity_log import setup_logging
from storageops.config import Config, load_config
from storageops.core import CleanupProblem, StorageAnalysis, analyze
from storageops.formatting import format_bytes, parse_size
from storageops.planners import (
    PLANNER_NAMES,
    Planner,
    PlannerResult,
    make_planner,
    plan_to_frame,
)
from storageops.sensors import (
    EnvironmentSnapshot,
    capture_environment,
    category_summary,
    duplicate_summary,
    largest_files,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="storageops",
        description="AI-based intelligent storage space optimization and safe cleanup planner.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="scan a folder (read-only) and build the file dataset")
    scan.add_argument("root", type=Path, help="folder to scan")
    scan.add_argument(
        "-o", "--output", type=Path, help="save the dataset (.csv or .parquet) plus a .meta.json"
    )
    scan.add_argument(
        "-c", "--config", type=Path, help="TOML config file (see config/default.toml)"
    )
    scan.add_argument("--no-duplicates", action="store_true", help="skip duplicate detection")
    scan.add_argument("--top", type=int, default=10, help="rows to show per table (default: 10)")
    scan.add_argument("-q", "--quiet", action="store_true", help="hide the progress indicator")
    scan.add_argument(
        "-y", "--yes", action="store_true",
        help="confirm without the interactive prompt (you still state your OK explicitly)",
    )  # fmt: skip
    scan.set_defaults(handler=_cmd_scan)

    ana = commands.add_parser(
        "analyze", help="score risk, safety and action options and formulate the search problem"
    )
    _add_problem_arguments(ana)
    ana.add_argument("-o", "--output", type=Path, help="save the per-file analysis table (.csv)")
    ana.set_defaults(handler=_cmd_analyze)

    plan = commands.add_parser("plan", help="search for a cleanup plan (proposes only)")
    _add_problem_arguments(plan)
    plan.add_argument(
        "--planner",
        choices=sorted(_PLANNER_CHOICES),
        default="bnb",
        help="planner to use (default: bnb = branch-and-bound)",
    )
    plan.add_argument(
        "--time-limit", type=float, default=10.0, help="branch-and-bound time limit in seconds"
    )
    plan.add_argument("--compare", action="store_true", help="also run and compare all planners")
    plan.add_argument("-o", "--output", type=Path, help="save the proposed plan (.csv)")
    plan.set_defaults(handler=_cmd_plan)

    bench = commands.add_parser(
        "bench", help="run the planner benchmark on synthetic instances (no real files)"
    )
    bench.add_argument(
        "-o", "--out", type=Path, default=Path("results"), help="output folder (default: results)"
    )
    bench.add_argument("--quick", action="store_true", help="small smoke-test run (< 1 minute)")
    bench.add_argument("--seeds", type=int, help="random instances per configuration")
    bench.add_argument("--time-limit", type=float, help="branch-and-bound limit per instance (s)")
    bench.add_argument("--no-scaling", action="store_true", help="skip the scaling study")
    bench.add_argument("--no-sensitivity", action="store_true", help="skip the weight study")
    bench.add_argument("-c", "--config", type=Path, help="TOML config file")
    bench.add_argument(
        "-y", "--yes", action="store_true",
        help="confirm without the interactive prompt (you still state your OK explicitly)",
    )  # fmt: skip
    bench.set_defaults(handler=_cmd_bench)

    gui = commands.add_parser("gui", help="open the desktop app (review, approve, execute)")
    gui.add_argument("source", nargs="?", help="folder or dataset to load on start")
    gui.add_argument("-c", "--config", type=Path, help="TOML config file")
    gui.set_defaults(handler=_cmd_gui)
    return parser


def _add_problem_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "source", type=Path, help="folder to scan, or a dataset saved by `scan -o`"
    )
    target = command.add_mutually_exclusive_group(required=True)
    target.add_argument("--free", type=_size, help="extra space to free, e.g. 20GB")
    target.add_argument("--target-free", type=_size, help="desired total free space, e.g. 100GB")
    command.add_argument("--move-to", help="destination folder on another drive (enables MOVE)")
    command.add_argument("--risk-budget", type=float, default=math.inf, help="max total plan cost")
    command.add_argument("-c", "--config", type=Path, help="TOML config file")
    command.add_argument("--top", type=int, default=10, help="rows to show (default: 10)")
    command.add_argument("-q", "--quiet", action="store_true", help="hide the progress indicator")
    command.add_argument(
        "-y", "--yes", action="store_true",
        help="confirm without the interactive prompt (you still state your OK explicitly)",
    )  # fmt: skip


_PLANNER_CHOICES = {
    name: (lambda args, name=name: make_planner(name, time_limit_s=args.time_limit))
    for name in PLANNER_NAMES
}


def _size(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    # Non-UTF-8 consoles must not crash on file names they cannot display.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if args.command != "gui":  # the GUI sets up its own logging
        setup_logging()
        shown = sys.argv[1:] if argv is None else list(argv)
        logging.getLogger(__name__).info("command: storageops %s", " ".join(shown))
    return args.handler(args)


def _cmd_scan(args: argparse.Namespace) -> int:
    config = _config_or_none(args.config)
    if config is None:
        return 2
    steps = [_read_step(args.root)]
    if args.output:
        steps.append(f"write the dataset to {args.output} (plus a .meta.json next to it)")
    if not _confirm(steps, args):
        return 1
    snapshot = _scan(args.root, config, quiet=args.quiet, duplicates=not args.no_duplicates)
    if snapshot is None:
        return 2

    _print_report(snapshot, args.top)
    if args.output:
        data_path, meta_path = snapshot.save(args.output)
        print(f"\nSaved dataset:  {data_path}\nSaved metadata: {meta_path}")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    steps = [_read_step(args.source), "score risk, safety and action options (in memory)"]
    if args.output:
        steps.append(f"write the analysis table to {args.output}")
    if not _confirm(steps, args):
        return 1
    loaded = _load_problem(args)
    if loaded is None:
        return 2
    snapshot, analysis, problem = loaded
    _print_analysis(snapshot, analysis, problem, args.top)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        analysis.to_frame().to_csv(args.output, index=False)
        print(f"\nSaved analysis: {args.output}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    steps = [
        _read_step(args.source),
        "compute a cleanup plan (a proposal only: no file is changed)",
    ]
    if args.output:
        steps.append(f"write the proposed plan to {args.output}")
    if not _confirm(steps, args):
        return 1
    loaded = _load_problem(args)
    if loaded is None:
        return 2
    snapshot, _, problem = loaded
    print(f"StorageOps plan for {snapshot.root}")
    print(
        f"Must free {format_bytes(problem.required_bytes)} "
        f"(free {format_bytes(problem.free_bytes)} -> target "
        f"{format_bytes(problem.target_free_bytes)}); {problem.n:,} candidate files"
    )

    chosen = _PLANNER_CHOICES[args.planner](args)
    names = list(_PLANNER_CHOICES) if args.compare else [args.planner]
    results = {name: _PLANNER_CHOICES[name](args).plan(problem) for name in names}
    if args.compare:
        _print_comparison(results)
    result = results[args.planner]
    _print_plan(result, snapshot.root, args.top, chosen)

    if args.output and result.plan.actions:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        plan_to_frame(result.plan).to_csv(args.output, index=False)
        print(f"\nSaved plan: {args.output}")
    print("\nNothing has been changed. Every action needs your approval before it runs.")
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    from storageops.gui.app import main as run_gui

    if _config_or_none(args.config) is None:
        return 2
    run_gui(args.source, args.config)
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from storageops.benchmark import ExperimentSettings, run_experiment

    config = _config_or_none(args.config)
    if config is None:
        return 2
    settings = ExperimentSettings.quick() if args.quick else ExperimentSettings()
    overrides = {
        "seeds": args.seeds,
        "time_limit_s": args.time_limit,
        "run_scaling": False if args.no_scaling else None,
        "run_sensitivity": False if args.no_sensitivity else None,
    }
    settings = replace(settings, **{k: v for k, v in overrides.items() if v is not None})
    steps = [
        "generate synthetic storage instances in memory (no real files are read or changed)",
        f"write CSV results, figures and summary.md to {args.out}",
    ]
    if not _confirm(steps, args):
        return 1
    run_experiment(args.out, settings, config=config, log=lambda m: print(m, flush=True))
    return 0


def _read_step(source: Path) -> str:
    if source.is_file():
        return f"read the saved dataset {source} (read-only)"
    return (
        f"scan {source}: read file names, sizes and dates, and read file contents to find "
        "duplicates (read-only: nothing is changed, moved or deleted)"
    )


def _confirm(steps: list[str], args: argparse.Namespace) -> bool:
    """Nothing runs without the user's OK: an interactive y/N, or an explicit --yes."""
    log = logging.getLogger(__name__)
    print("StorageOps will:", file=sys.stderr)
    for step in steps:
        print(f"  - {step}", file=sys.stderr)
    if args.yes:
        log.info("user confirmed with --yes: %s", "; ".join(steps))
        return True
    if not sys.stdin.isatty():
        print(
            "error: no terminal to confirm in. Re-run with --yes to give your OK explicitly.",
            file=sys.stderr,
        )
        log.warning("refused: no confirmation possible (no terminal, no --yes)")
        return False
    try:
        answer = input("Continue? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    confirmed = answer in ("y", "yes")
    log.info("user answered %r: %s", answer, "confirmed" if confirmed else "cancelled")
    if not confirmed:
        print("Cancelled. Nothing was done.", file=sys.stderr)
    return confirmed


def _load_problem(
    args: argparse.Namespace,
) -> tuple[EnvironmentSnapshot, StorageAnalysis, CleanupProblem] | None:
    """Shared by analyze/plan: load or scan the source, analyse it, build the problem."""
    config = _config_or_none(args.config)
    if config is None:
        return None
    if args.source.is_file():
        try:
            snapshot = EnvironmentSnapshot.load(args.source)
        except (OSError, ValueError, KeyError) as exc:
            print(f"error: cannot load dataset: {exc}", file=sys.stderr)
            return None
    else:
        snapshot = _scan(args.source, config, quiet=args.quiet, duplicates=True)
        if snapshot is None:
            return None

    free = snapshot.disk.free_bytes
    target = args.target_free if args.target_free is not None else free + args.free
    analysis = analyze(snapshot.files, config, move_destination=args.move_to)
    problem = CleanupProblem.from_analysis(
        analysis, free_bytes=free, target_free_bytes=target, risk_budget=args.risk_budget
    )
    return snapshot, analysis, problem


def _config_or_none(path: Path | None) -> Config | None:
    try:
        return load_config(path)
    except (OSError, ValueError) as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        return None


def _scan(
    root: Path, config: Config, *, quiet: bool, duplicates: bool
) -> EnvironmentSnapshot | None:
    # A carriage-return progress line only makes sense on an interactive terminal.
    progress = None if quiet or not sys.stderr.isatty() else _ProgressPrinter()
    try:
        return capture_environment(root, config, detect_duplicates=duplicates, progress=progress)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    finally:
        if progress:
            progress.finish()


class _ProgressPrinter:
    def __call__(self, files_found: int, _directory: str) -> None:
        print(f"\r  scanning... {files_found:,} files", end="", file=sys.stderr, flush=True)

    def finish(self) -> None:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)


def _print_report(snapshot: EnvironmentSnapshot, top: int) -> None:
    files, disk, stats = snapshot.files, snapshot.disk, snapshot.scan_stats
    captured = datetime.fromtimestamp(snapshot.captured_at).strftime("%Y-%m-%d %H:%M:%S")

    print(f"StorageOps scan of {snapshot.root}  ({captured})")
    print(
        f"Volume {disk.volume}  free {format_bytes(disk.free_bytes)} of "
        f"{format_bytes(disk.total_bytes)} ({disk.free_ratio:.1%})"
    )
    print(
        f"Scanned {stats.files_found:,} files ({format_bytes(snapshot.total_bytes)}) in "
        f"{stats.dirs_visited:,} folders, {stats.duration_s:.2f}s; "
        f"skipped {stats.files_skipped:,} files / {stats.dirs_skipped:,} folders"
    )
    if files.empty:
        print("\nNo eligible files found.")
        return

    by_category = category_summary(files)
    by_category["total_bytes"] = by_category["total_bytes"].map(format_bytes)
    by_category["share"] = by_category["share"].map("{:.1%}".format)
    by_category["median_age_days"] = by_category["median_age_days"].map("{:.0f}".format)
    print("\nBy category:")
    print(by_category.to_string(index=False))

    biggest = largest_files(files, top).copy()
    biggest["path"] = biggest["path"].map(lambda p: _relative(p, snapshot.root))
    biggest["size_bytes"] = biggest["size_bytes"].map(format_bytes)
    biggest["age_days"] = biggest["age_days"].map("{:.0f}".format)
    print(f"\nLargest {len(biggest)} files:")
    print(biggest.to_string(index=False))

    if snapshot.duplicates is not None:
        dup = snapshot.duplicates
        print(
            f"\nDuplicates: {dup.groups:,} groups, {dup.duplicate_files:,} redundant copies, "
            f"{format_bytes(dup.reclaimable_bytes)} reclaimable "
            f"(read {format_bytes(dup.bytes_read)} from {dup.files_hashed:,} candidates)"
        )
        groups = duplicate_summary(files).head(top)
        if not groups.empty:
            groups["size_bytes"] = groups["size_bytes"].map(format_bytes)
            groups["reclaimable_bytes"] = groups["reclaimable_bytes"].map(format_bytes)
            groups["original_path"] = groups["original_path"].map(
                lambda p: _relative(p, snapshot.root)
            )
            print(groups.to_string(index=False))

    if snapshot.issues:
        print(f"\n{len(snapshot.issues):,} paths could not be read, e.g.:")
        for issue in snapshot.issues[:5]:
            print(f"  {issue.path}: {issue.message}")


def _print_analysis(
    snapshot: EnvironmentSnapshot, analysis: StorageAnalysis, problem: CleanupProblem, top: int
) -> None:
    files, risk = analysis.files, analysis.risk["risk"]
    print(f"StorageOps analysis of {snapshot.root}")
    print(
        f"Free {format_bytes(problem.free_bytes)}, target {format_bytes(problem.target_free_bytes)}"
        f" -> must free {format_bytes(problem.required_bytes)}"
    )

    reasons = analysis.protection["protection_reason"]
    protected = reasons[reasons != ""].value_counts()
    no_option = int((~analysis.candidates).sum()) - int(protected.sum())
    print(
        f"\nFiles: {len(files):,} total, {problem.n:,} candidates, "
        f"{int(protected.sum()):,} protected, {no_option:,} with no safe action"
    )
    for reason, count in protected.items():
        print(f"  protected ({reason}): {count:,}")

    bands = pd.cut(risk, [-0.01, 0.2, 0.5, 1.0], labels=["low (<0.2)", "medium", "high (>=0.5)"])
    band_bytes = files["size_bytes"].groupby(bands, observed=False).sum()
    print("\nRisk:")
    for band, count in bands.value_counts(sort=False).items():
        print(f"  {band:<13} {count:>8,} files  {format_bytes(band_bytes[band]):>10}")

    actions = analysis.action_summary()
    actions["potential_bytes"] = actions["potential_bytes"].map(format_bytes)
    actions["mean_cost"] = actions["mean_cost"].map("{:.3f}".format)
    print("\nAction options (files where allowed, space the action alone could free):")
    print(actions.to_string())

    shortfall = problem.required_bytes - problem.max_freeable_bytes
    verdict = "feasible" if shortfall <= 0 else f"INFEASIBLE, short by {format_bytes(shortfall)}"
    print(
        f"\nSearch problem: {problem.n:,} decision variables, "
        f"~10^{problem.state_space_log10:.1f} complete plans, "
        f"up to {format_bytes(problem.max_freeable_bytes)} freeable -> {verdict}"
    )

    if problem.n:
        table = analysis.to_frame()
        best = table[table["best_action"] != "KEEP"].nlargest(top, "best_score")
        view = pd.DataFrame(
            {
                "path": best["path"].map(lambda p: _relative(p, snapshot.root)),
                "size": best["size_bytes"].map(format_bytes),
                "category": best["category"].astype(str),
                "risk": best["risk"].map("{:.2f}".format),
                "action": best["best_action"],
                "frees": best["best_benefit"].map(format_bytes),
                "h": best["best_score"].map("{:.3f}".format),
            }
        )
        print(f"\nTop {len(view)} candidates by heuristic:")
        print(view.to_string(index=False))


def _print_comparison(results: dict[str, PlannerResult]) -> None:
    rows = []
    for name, r in results.items():
        rows.append(
            {
                "planner": name,
                "status": r.status,
                "frees": format_bytes(r.plan.freed_bytes),
                "cost": f"{r.plan.total_cost:.3f}",
                "actions": len(r.plan.actions),
                "time": f"{r.stats.elapsed_s:.2f}s",
                "generated": r.stats.nodes_generated,
                "expanded": r.stats.nodes_expanded,
                "pruned": r.stats.nodes_pruned,
                "gap": "-" if math.isnan(r.gap) else f"{r.gap:.2%}",
            }
        )
    print("\nPlanner comparison:")
    print(pd.DataFrame(rows).to_string(index=False))


def _print_plan(result: PlannerResult, root: str, top: int, planner: Planner) -> None:
    plan, stats = result.plan, result.stats
    print(f"\nPlan from {planner.name}: {result.status}")
    if result.status == "infeasible":
        print("  The target cannot be reached with the allowed, safe actions.")
        return
    if not plan.actions:
        print("  No action needed: the target is already met.")
        return
    gap = "" if math.isnan(result.gap) else f", optimality gap {result.gap:.2%}"
    print(
        f"  frees {format_bytes(plan.freed_bytes)} with {len(plan.actions):,} actions, "
        f"total cost {plan.total_cost:.3f}{gap}"
    )
    print(
        f"  search: {stats.nodes_generated:,} generated, {stats.nodes_expanded:,} expanded, "
        f"{stats.nodes_pruned:,} pruned in {stats.elapsed_s:.2f}s"
        + (" (limit reached)" if stats.limit_reached else "")
    )
    if not plan.meets_target:
        short = plan.required_bytes - plan.freed_bytes
        print(f"  WARNING: {format_bytes(short)} short of the target")

    frame = plan_to_frame(plan)
    by_action = frame.groupby("action").agg(files=("path", "size"), frees=("benefit_bytes", "sum"))
    for action, row in by_action.iterrows():
        print(f"  {action:<9} {row['files']:>6,} files  {format_bytes(row['frees']):>10}")

    shown = frame.nlargest(top, "benefit_bytes")
    view = pd.DataFrame(
        {
            "path": shown["path"].map(lambda p: _relative(p, root)),
            "action": shown["action"],
            "size": shown["size_bytes"].map(format_bytes),
            "frees": shown["benefit_bytes"].map(format_bytes),
            "risk": shown["risk"].map("{:.2f}".format),
            "cost": shown["cost"].map("{:.3f}".format),
        }
    )
    print(f"\nLargest {len(view)} of {len(frame):,} proposed actions:")
    print(view.to_string(index=False))


def _relative(path: str, root: str) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path
