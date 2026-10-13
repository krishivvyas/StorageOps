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

All options (excluded folders, hashing parameters, ...) are documented in
[config/default.toml](config/default.toml).

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
