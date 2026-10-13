"""Command-line entry point: ``storageops <command>``.

Phase 1 provides ``scan``, Phase 2 ``analyze``. Later phases add ``plan``, ``bench``
and ``gui``.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pandas as pd

from storageops import __version__
from storageops.config import Config, load_config
from storageops.core import CleanupProblem, StorageAnalysis, analyze
from storageops.formatting import format_bytes, parse_size
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
    scan.set_defaults(handler=_cmd_scan)

    ana = commands.add_parser(
        "analyze", help="score risk, safety and action options and formulate the search problem"
    )
    ana.add_argument("source", type=Path, help="folder to scan, or a dataset saved by `scan -o`")
    target = ana.add_mutually_exclusive_group(required=True)
    target.add_argument("--free", type=_size, help="extra space to free, e.g. 20GB")
    target.add_argument("--target-free", type=_size, help="desired total free space, e.g. 100GB")
    ana.add_argument("--move-to", help="destination folder on another drive (enables MOVE)")
    ana.add_argument("--risk-budget", type=float, default=math.inf, help="max total plan cost")
    ana.add_argument("-o", "--output", type=Path, help="save the per-file analysis table (.csv)")
    ana.add_argument("-c", "--config", type=Path, help="TOML config file")
    ana.add_argument("--top", type=int, default=10, help="candidates to show (default: 10)")
    ana.add_argument("-q", "--quiet", action="store_true", help="hide the progress indicator")
    ana.set_defaults(handler=_cmd_analyze)
    return parser


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
    return args.handler(args)


def _cmd_scan(args: argparse.Namespace) -> int:
    config = _config_or_none(args.config)
    if config is None:
        return 2
    snapshot = _scan(args.root, config, quiet=args.quiet, duplicates=not args.no_duplicates)
    if snapshot is None:
        return 2

    _print_report(snapshot, args.top)
    if args.output:
        data_path, meta_path = snapshot.save(args.output)
        print(f"\nSaved dataset:  {data_path}\nSaved metadata: {meta_path}")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    config = _config_or_none(args.config)
    if config is None:
        return 2
    if args.source.is_file():
        try:
            snapshot = EnvironmentSnapshot.load(args.source)
        except (OSError, ValueError, KeyError) as exc:
            print(f"error: cannot load dataset: {exc}", file=sys.stderr)
            return 2
    else:
        snapshot = _scan(args.source, config, quiet=args.quiet, duplicates=True)
        if snapshot is None:
            return 2

    free = snapshot.disk.free_bytes
    target = args.target_free if args.target_free is not None else free + args.free
    analysis = analyze(snapshot.files, config, move_destination=args.move_to)
    problem = CleanupProblem.from_analysis(
        analysis, free_bytes=free, target_free_bytes=target, risk_budget=args.risk_budget
    )
    _print_analysis(snapshot, analysis, problem, args.top)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        analysis.to_frame().to_csv(args.output, index=False)
        print(f"\nSaved analysis: {args.output}")
    return 0


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


def _relative(path: str, root: str) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path
