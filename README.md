# StorageOps

AI-Based Intelligent Storage Space Optimization and Safe Cleanup Planner.
ECSCT24301 Foundation of AI, PBL (Div C): Het Patel, Krishiv Vyas, Parth Patel.

StorageOps treats storage cleanup as an AI planning and optimization problem rather than a
fixed rule list. It scans a storage environment, models every eligible file's possible
actions (KEEP / DELETE / ARCHIVE / COMPRESS / MOVE) with their benefit and risk, and searches
for a cleanup plan that reaches a user-defined free-space target safely. **No file is ever
modified without explicit user approval.** See [docs/problem_formulation.md](docs/problem_formulation.md).

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -e ".[dev,parquet]"
```

## Usage

Scanning is strictly read-only.

```bash
storageops scan "C:\Users\me\Downloads"                      # print an analysis report
storageops scan "C:\Users\me\Downloads" -o data/downloads.csv # also save the dataset
storageops scan D:\ --config my.toml --no-duplicates --top 20
```

`-o` writes the per-file table (`.csv` or `.parquet`) plus a `<name>.meta.json` sidecar with
volume usage, scan statistics and unreadable paths. Reload both with
`EnvironmentSnapshot.load(path)`.

From Python:

```python
from storageops.sensors import capture_environment, category_summary

snapshot = capture_environment("C:/Users/me/Downloads")
print(snapshot.disk.free_bytes, len(snapshot.files))
print(category_summary(snapshot.files))
```

### Analysis (Phase 2)

`analyze` scores every file's risk, applies the safety rules, works out the benefit and
cost of each action, and formulates the search problem for a free-space target. It is
read-only and accepts either a folder or a dataset saved by `scan -o`.

```bash
storageops analyze "C:\Users\me\Downloads" --free 5GB             # free 5 GB more
storageops analyze data/downloads.csv --target-free 100GB         # reach 100 GB free
storageops analyze data/downloads.csv --free 5GB --move-to E:\Archive -o data/analysis.csv
```

```python
from storageops.core import CleanupProblem, analyze

analysis = analyze(snapshot.files, move_destination="E:/Archive")
problem = CleanupProblem.from_analysis(
    analysis, free_bytes=snapshot.disk.free_bytes, target_free_bytes=100 * 1024**3
)
state = problem.initial_state()
for action, child in problem.successors(state):  # the search interface used by the planners
    ...
```

All options (excluded folders, hashing, risk weights, action costs, heuristic weights,
protected paths, ...) are documented in [config/default.toml](config/default.toml).

## How the sensors work

- **Scanner** (`sensors/scanner.py`): iterative `os.scandir` walk, so no recursion limit.
  It does not follow symlinks or NTFS junctions, skips cloud-only placeholder files, and
  records unreadable paths instead of crashing.
- **Metadata** (`sensors/metadata.py`): size, created / modified / accessed times, age, hidden,
  system and read-only flags, and a category. The category comes from the extension, or from
  folder hints such as `Temp` or `Cache` (only the scan root and the folders below it are used).
- **Duplicates** (`sensors/hashing.py`): narrowed in three stages (size, then head+tail
  partial hash, then full BLAKE2b hash) so only true candidates are read in full. Hard links
  are not counted as reclaimable.
- **Dataset** (`sensors/dataset.py`): typed pandas DataFrame plus category and duplicate summaries.

## How the analysis works

- **Risk** (`core/risk.py`): a value between 0 and 1. It combines the category's base risk,
  how recently the file was modified and opened, the folder it lives in (Documents vs.
  Downloads), its hidden/read-only flags, and a discount for duplicates.
- **Safety** (`core/safety.py`): protected locations, system files and sensitive name
  patterns such as keys and password vaults may only be KEPT.
- **Actions** (`core/actions.py`): benefit and cost of every (file, action) pair, with
  cost = severity x risk + penalty. Disallowed pairs cost infinity.
- **Heuristic** (`core/heuristic.py`): a weighted score that guides the search order; it
  never changes which plan is optimal.
- **State space** (`core/state.py`): `CleanupProblem` / `PlanState` with initial state,
  successors, goal test, path cost and the duplicate-group constraint.

Full formulas: [docs/problem_formulation.md](docs/problem_formulation.md).

## Development

```bash
pytest              # tests use synthetic file trees only, never real user data
ruff check . && ruff format --check .
```

## Repository layout

```
src/storageops/
  models.py      shared types: FileRecord, Action, FileCategory
  config.py      TOML configuration with validation
  sensors/       Phase 1 - perception (read-only)
  core/          Phase 2 - risk, heuristic, state space
  planners/      Phase 3 - greedy & branch-and-bound
  benchmark/     Phase 4 - synthetic instances & evaluation
  executor/      Phase 5 - approved file operations
  gui/           Phase 5 - tkinter interface
tests/           mirrors src/
config/          default configuration
docs/            formulation & analysis write-ups
```
