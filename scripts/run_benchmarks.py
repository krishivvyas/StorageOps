"""Run the full StorageOps benchmark and write results/ (CSV, figures, summary.md).

Usage:
    python scripts/run_benchmarks.py            # full experiment
    python scripts/run_benchmarks.py --quick    # smoke test
Any `storageops bench` option is accepted.
"""

import sys

from storageops.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["bench", *sys.argv[1:]]))
