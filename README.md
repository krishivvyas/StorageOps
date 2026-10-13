# StorageOps

AI-Based Intelligent Storage Space Optimization and Safe Cleanup Planner.
ECSCT24301 Foundation of AI, PBL (Div B): Het Patel, Krishiv Vyas, Parth Patel.

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

**Nothing happens without your OK.** Every command first lists exactly what it will do
(read, compute, write a file) and asks `Continue? [y/N]`. Pass `--yes` to give that OK
up front, e.g. in scripts. Without a terminal and without `--yes`, a command refuses to run.
In the desktop app, every step (scan, plan, re-plan, dry run, execute) asks for OK in a
dialog first. Scanning is strictly read-only.

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

### Planning (Phase 3)

`plan` searches for the cheapest set of actions that reaches the target. It only proposes:
nothing is changed on disk.

```bash
storageops plan data/downloads.csv --free 5GB                        # branch-and-bound (default)
storageops plan data/downloads.csv --free 5GB --compare              # greedy vs. branch-and-bound
storageops plan data/downloads.csv --free 5GB --planner greedy -o data/plan.csv
storageops plan data/downloads.csv --free 50GB --time-limit 60 --risk-budget 20
```

```python
from storageops.planners import BranchAndBoundPlanner, GreedyPlanner

result = BranchAndBoundPlanner(time_limit_s=10).plan(problem)
print(result.status, result.plan.freed_bytes, result.plan.total_cost, result.gap)
print(result.stats)  # nodes generated / expanded / pruned, time
```

### Benchmarking (Phase 4)

`bench` compares the planners on synthetic storage environments. These are generated file
tables with realistic sizes, ages, folders and duplicates, so no real files are involved.
It writes CSVs, figures and a Markdown summary.

```bash
storageops bench --quick                 # smoke test, a few seconds
storageops bench -o results              # full experiment (several minutes)
python scripts/run_benchmarks.py --seeds 5 --time-limit 10
```

Outputs in `results/`:
- `results.csv`: every (instance, planner) run;
- `scaling.csv`: runs at growing file counts;
- `sensitivity.csv`: the heuristic-weight sweep;
- `figures/*.png`: quality, scaling time, scaling nodes and sensitivity charts;
- `summary.md`: the tables behind every figure.

The latest numbers and figures are in [docs/benchmark_results.md](docs/benchmark_results.md);
the written analysis is in [docs/failure_analysis.md](docs/failure_analysis.md).
Test results (557 passed, 1 skipped) are in [docs/test_results.md](docs/test_results.md).

### Desktop app: review and approve (Phase 5)

```bash
storageops gui                            # choose a folder or dataset in the window
storageops gui "C:\Users\me\Downloads"    # pre-fill the folder (still asks before scanning)
```

Each step below starts only after you press OK in a dialog that says exactly what it
will do. Cancel at any point and nothing happens.

1. **Load:** scan a folder (read-only) or open a saved dataset. The *Overview* tab shows
   space by category; *Files & risk* shows every file's risk, protection reason and best
   action.
2. **Plan:** set the target ("free an extra 5GB", or "reach 100GB free"), optionally a MOVE
   destination on another drive, and a planner.
3. **Review:** in *Plan review* every proposed action starts **unapproved**. Approve rows
   (click ☐ or press Space) or all at once. *Reject selected & re-plan* keeps the chosen
   files and searches for an alternative plan.
4. **Dry run:** required, for exactly the current selection. It checks every file and
   changes nothing. Changing the selection afterwards requires a new dry run.
5. **Execute:** asks for confirmation, then performs only the approved actions.

### Safety: what touching a file involves

| Layer | Guarantee |
|---|---|
| Your OK | Every step (scan, plan, re-plan, dry run, execute) needs explicit confirmation. The workflow core (`Session`) refuses any step without it, and logs both confirmations and refusals |
| Approval | Only actions the user approved can run, and only after a dry run of that exact selection |
| Preflight | A file is skipped if it is missing, changed since the scan, under a protected location, or has the system attribute |
| DELETE | Goes to the Recycle Bin, never a permanent delete |
| ARCHIVE / MOVE | The original is removed only after the archive or copy has been re-read and its content hash matches |
| COMPRESS | NTFS compression in place; the content hash is checked to be unchanged |
| Failures | One failed file never stops or affects the others; the original stays in place |

### Logs: a record of everything that happened

StorageOps keeps two records, both under `~/.storageops` (set the `STORAGEOPS_HOME`
environment variable to use another folder):

- `logs/storageops.log`: activity log of every command and GUI session. It records scans,
  plans, approvals, rejections, dry runs and every file operation with its outcome.
  Rotated at 2 MB, with 10 old files kept.
- `journal/*.jsonl`: one file per dry run or execution, with one JSON line per file:
  path, action, status, bytes freed, and where the data went (Recycle Bin, archive path or
  move destination). Use it to audit a run or to restore files by hand.

The GUI's *Results* tab shows both paths and has an *Open logs & journals* button. The test
suite redirects `STORAGEOPS_HOME` to a temporary folder, so tests never write here.

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


## How the planners work

- **Greedy** (`planners/greedy.py`): the multiple-choice knapsack greedy. It takes the
  cheapest bytes first, walking each file's convex hull of actions (KEEP -> COMPRESS ->
  DELETE). Fast, with no guarantee.
- **Branch-and-bound** (`planners/branch_and_bound.py`): depth-first search starting from
  the greedy plan, pruned with an LP-relaxation lower bound. It proves optimality when it
  finishes; otherwise it reports the gap.
- **Exhaustive** (`planners/exhaustive.py`): brute force for tiny instances, the ground truth
  for tests and benchmarks.

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
  executor/      Phase 5 - approved file operations + journal
  gui/           Phase 5 - tkinter app (session.py = workflow, app.py = window)
  paths.py       where logs and journals go (STORAGEOPS_HOME)
  activity_log.py  always-on activity log
tests/           mirrors src/
config/          default configuration
docs/            formulation & analysis write-ups
```
