# StorageOps: Test Results

## Summary

| | |
|---|---|
| Result | **557 passed, 1 skipped, 0 failed** |
| Tests collected | 558 |
| Duration | 21.4 s |
| Date | 13 Oct 2026 |
| Platform | Windows 11 (win32), Python 3.12.7, pytest 9.1.1 |
| Command | `.venv\Scripts\python -m pytest` (run from `storageops/`) |

The skipped test needs to create symbolic links, which Windows only allows with Developer
Mode or administrator rights (see [Skipped test](#skipped-test)).

## Safety of the test suite itself

The tests never touch real user data:

- **Files:** every test works on synthetic files that it creates itself in pytest's
  temporary folder.
- **Recycle Bin:** "deleted" files go to a fake bin folder inside that temp folder, never
  to the real Recycle Bin.
- **Logs and journals:** an automatic fixture (`tests/conftest.py`) points
  `STORAGEOPS_HOME` at a fresh temporary folder for every test, so no test writes into
  `~/.storageops`. This was verified: after a full run, `~/.storageops` does not exist.
- **GUI tests:** they run the real window, hidden, and answer its dialogs automatically.

## Results by area

| Area | Test file | Tests | Result |
|---|---|---|---|
| **Phase 1: Sensors** | `sensors/test_scanner.py` | 9 | 8 passed, 1 skipped |
| | `sensors/test_metadata.py` | 12 | passed |
| | `sensors/test_hashing.py` | 9 | passed |
| | `sensors/test_dataset.py` | 8 | passed |
| | `sensors/test_environment.py` | 3 | passed |
| **Phase 2: Heuristics & state space** | `core/test_risk.py` | 7 | passed |
| | `core/test_safety.py` | 6 | passed |
| | `core/test_actions_heuristic.py` | 10 | passed |
| | `core/test_analysis.py` | 5 | passed |
| | `core/test_state.py` | 15 | passed |
| **Phase 3: Planners** | `planners/test_planners.py` | 284 | passed |
| | `planners/test_relaxation.py` | 95 | passed |
| **Phase 4: Benchmark** | `benchmark/test_benchmark.py` | 16 | passed |
| **Phase 5: Executor & GUI** | `executor/test_executor.py` | 13 | passed |
| | `gui/test_session.py` | 8 | passed |
| | `gui/test_app.py` | 6 | passed |
| | `test_user_confirmation.py` | 10 | passed |
| | `test_activity_log.py` | 4 | passed |
| **Shared** | `test_config.py` | 17 | passed |
| | `test_cli.py` | 7 | passed |
| | `test_formatting.py` | 14 | passed |
| **Total** | 21 files | **558** | **557 passed, 1 skipped** |

## What is verified

### Phase 1: Sensors (41 tests)

- **Scanner:**
  - finds every file, and skips excluded folders (e.g. `.git`), hidden files when
    configured, too-small files and excluded extensions;
  - respects the depth limit, reports progress, and does not follow symbolic links;
  - rejects missing or non-folder roots;
  - takes folder-name hints (Temp, Cache) only from the scanned folder downwards, so a
    project stored under a `Temp` folder isn't mislabelled.
- **Metadata:** maps extensions and folders to categories, computes ages, flags hidden
  files, and treats future timestamps as age 0.
- **Duplicates:**
  - identical files are grouped and the oldest copy is kept as the original;
  - same-size files with different content are not grouped, even when they differ only in
    the middle;
  - empty files and hard links are not counted as reclaimable space;
  - a file that disappears during hashing is reported, not fatal.
- **Datasets:** CSV and Parquet round-trips are exact, including tricky names such as a
  file called `NA` or one without an extension; the category and duplicate summaries are
  correct.

### Phase 2: Heuristics & state space (43 tests)

- **Risk:** each component of the formula (category, recency, usage, folder, flags,
  duplicate discount) is checked against hand-computed values, with clipping to [0, 1].
- **Safety:**
  - protected folders are matched case-insensitively and respect folder boundaries
    (`C:\Windows` does not protect `C:\Windows2`);
  - environment variables are expanded;
  - system files and sensitive names (keys, certificates, password vaults) are always
    protected.
- **Actions & heuristic:**
  - exact benefit and cost per action;
  - risky files cannot be deleted but may be archived;
  - already-compressed media is never re-compressed;
  - MOVE needs a destination on another drive;
  - the heuristic orders candidates as designed.
- **State space:**
  - every complete plan generated through `successors()` is checked against brute-force
    enumeration;
  - the "never delete every copy of a duplicate" rule, the goal test, the risk budget,
    plan evaluation and reordering all behave correctly.

### Phase 3: Planners (379 tests)

- **Branch-and-bound vs. exhaustive search:**
  - 60 random instances × 4 configurations, all matching exhaustive search exactly: same
    optimal cost, same infeasibility verdict, never more search effort;
  - another 15 instances × groups of 2 and 3 copies × in-order or scattered positions,
    each checked with 6 configurations.
- **Lower bound:** never exceeds the true optimum (30 instances). The duplicate-group hull
  is checked against every feasible combination of a 3-copy group.
- **Greedy:**
  - its plans never break a constraint and never beat the optimum;
  - the known "overshoot trap", where greedy is beaten by branch-and-bound, is reproduced;
  - upgrading an action, removing redundant actions and the heuristic ordering are tested.
- **Limits:** the time and node limits return the best plan found together with a valid
  optimality gap.

### Phase 4: Benchmark (16 tests)

- Instance generation is deterministic, and every instance gets its own random stream. A
  regression test covers an earlier bug where the seeds collided.
- Duplicates follow the same rules as the real detector, and the risk levels shift the
  category mix and file ages as intended.
- The quality metrics handle infeasible instances, and the summary averages only
  successful plans.
- The full experiment writes all its CSVs, figures and the summary.

### Phase 5: Executor & GUI (41 tests)

- **Executor:**
  - dry run is the default and changes nothing;
  - DELETE goes to the bin;
  - ARCHIVE and MOVE remove the original only after the copy has been verified; if
    verification fails, the original stays and the bad archive is removed;
  - real NTFS compression keeps the file readable;
  - files that changed or disappeared since the scan are skipped;
  - protected locations are refused even if approved;
  - one failure does not stop the other files, and Cancel stops before the next file.
- **Workflow (`Session`):**
  - a new plan starts with nothing approved;
  - execution requires a dry run of exactly the current selection, and changing the
    selection invalidates that dry run;
  - a re-scan is required after execution;
  - rejected files are kept when re-planning.
- **User confirmation:**
  - every step (load, plan, re-plan, dry run, execute) refuses to run without the user's
    explicit OK;
  - the CLI refuses without a terminal or `--yes`, and the y/N prompt is honoured;
  - the GUI does nothing on launch, and pressing Cancel at any step leaves everything
    unchanged.
- **Logs:**
  - every step, confirmation and file operation is written to the activity log, and each
    run has its own journal;
  - tests never write to the real `~/.storageops`.
- **GUI:** the full workflow runs in the real (hidden) window, plus row toggling,
  reject-and-re-plan, the explanation shown for infeasible targets, the filters, and
  relative path display.

### Shared modules (38 tests)

- **Configuration:** the shipped `config/default.toml` matches the built-in defaults;
  partial overrides merge with the defaults; unknown keys and invalid values are rejected.
- **CLI:** `scan`, `analyze` and `plan` (including `--compare`, saving output and reporting
  infeasible targets) and error handling.
- **Formatting:** sizes such as `1.5 g` and `750MiB` are parsed and formatted correctly,
  and invalid input is rejected.

## Skipped test

`tests/sensors/test_scanner.py::test_symlinks_are_not_followed_by_default` is skipped
because creating symbolic links on Windows needs Developer Mode or administrator rights.
The behaviour it covers (symlinks and NTFS junctions are not followed) is still in the
scanner. Turning on Developer Mode, or running on Linux or macOS, runs the test.

## Code quality checks

| Check | Command | Result |
|---|---|---|
| Lint (pyflakes, pycodestyle, bugbear, pyupgrade, simplify, isort) | `ruff check .` | All checks passed |
| Formatting | `ruff format --check .` | All files formatted |

## Reproducing

```bash
cd storageops
.venv\Scripts\activate
python -m pytest            # full suite, ~20 s
python -m pytest -rs        # also list the reasons for skipped tests
python -m pytest tests/planners -q   # one area only
```

## Raw output

```text
platform win32 -- Python 3.12.7, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Krishiv Personal Folder\practicle\3rdyear\fai\storageops
configfile: pyproject.toml
testpaths: tests
collected 558 items

tests\benchmark\test_benchmark.py ................                     [  2%]
tests\core\test_actions_heuristic.py ..........                        [  4%]
tests\core\test_analysis.py .....                                      [  5%]
tests\core\test_risk.py .......                                        [  6%]
tests\core\test_safety.py ......                                       [  7%]
tests\core\test_state.py ...............                               [ 10%]
tests\executor\test_executor.py .............                          [ 12%]
tests\gui\test_app.py ......                                           [ 13%]
tests\gui\test_session.py ........                                     [ 15%]
tests\planners\test_planners.py ...................................... [ 26%]
  (284 tests in total)                                                 [ 66%]
tests\planners\test_relaxation.py .................................... [ 77%]
  (95 tests in total)                                                  [ 83%]
tests\sensors\test_dataset.py ........                                 [ 84%]
tests\sensors\test_environment.py ...                                  [ 85%]
tests\sensors\test_hashing.py .........                                [ 86%]
tests\sensors\test_metadata.py ............                            [ 89%]
tests\sensors\test_scanner.py .......s.                                [ 90%]
tests\test_activity_log.py ....                                        [ 91%]
tests\test_cli.py .......                                              [ 92%]
tests\test_config.py .................                                 [ 95%]
tests\test_formatting.py ..............                                [ 98%]
tests\test_user_confirmation.py ..........                             [100%]

SKIPPED [1] tests\sensors\test_scanner.py:90: creating symlinks requires extra privileges on this system
========================= 557 passed, 1 skipped in 21.41s =========================
```
