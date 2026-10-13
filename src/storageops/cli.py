"""Command-line entry point: ``storageops <command>``.

Phase 1 provides ``scan``. Later phases add ``plan``, ``bench`` and ``gui``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from storageops import __version__
from storageops.config import load_config
from storageops.formatting import format_bytes
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Non-UTF-8 consoles must not crash on file names they cannot display.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    return args.handler(args)


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        return 2

    # A carriage-return progress line only makes sense on an interactive terminal.
    progress = None if args.quiet or not sys.stderr.isatty() else _ProgressPrinter()
    try:
        snapshot = capture_environment(
            args.root, config, detect_duplicates=not args.no_duplicates, progress=progress
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if progress:
            progress.finish()

    _print_report(snapshot, args.top)
    if args.output:
        data_path, meta_path = snapshot.save(args.output)
        print(f"\nSaved dataset:  {data_path}\nSaved metadata: {meta_path}")
    return 0


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


def _relative(path: str, root: str) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path
