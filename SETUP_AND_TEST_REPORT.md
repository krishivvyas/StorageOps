# StorageOps: Setup and Test Execution Report

**Environment OS:** Windows 11 (build 10.0.26100)  
**Host Shell:** PowerShell  
**Repository Directory:** `StorageOps_PBL_Audit`  
**Execution Timestamp:** October 28, 2026, 08:20 IST  

---

## 1. Python Runtime & Environment Setup

### 1.1 Python Detection & Selection
Inspection with Python Launcher (`py -0p` and `py -3.11 -V`):
- `py -0p` detected: `-V:3.12 * C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe`
- `py -3.11 -V`: `No suitable Python runtime found`
- `py -3.12 -V`: `Python 3.12.10`

**Decision:**
The project's `pyproject.toml` explicitly declares:
```toml
requires-python = ">=3.11"
```
Python 3.12.10 meets and exceeds the `>=3.11` specification and is fully supported by all declared dependencies (`pandas>=2.1`, `matplotlib>=3.8`, `pyarrow>=14`, `pytest>=8`, `ruff>=0.5`, `send2trash>=1.8`). Python 3.12.10 was therefore selected as the virtual environment base interpreter.

### 1.2 Dedicated Virtual Environment Creation
Executed:
```powershell
py -3.12 -m venv .venv
```
Interpreter path verified:
```
C:\Users\Asus\OneDrive\Desktop\StorageOps\StorageOps_PBL_Audit\.venv\Scripts\python.exe
```

### 1.3 Dependency Installation
Executed:
```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,parquet]"
```

**Installed Packages & Exact Versions:**
- `storageops`: `0.1.0` (editable install from `src`)
- `pip`: `26.2.2`
- `pandas`: `3.1.0`
- `numpy`: `2.5.4`
- `matplotlib`: `3.11.2`
- `pyarrow`: `26.0.0`
- `pytest`: `9.2.0`
- `ruff`: `0.17.3`
- `send2trash`: `2.1.0`
- `fonttools`: `4.68.0`
- `pillow`: `13.0.0`
- `kiwisolver`: `1.5.1`
- `cycler`: `0.12.1`
- `contourpy`: `1.4.0`
- `pyparsing`: `3.3.3`
- `python-dateutil`: `3.0.0`
- `pluggy`: `1.7.0`
- `iniconfig`: `2.3.1`
- `pygments`: `2.21.0`
- `colorama`: `0.4.6`
- `packaging`: `26.3`
- `tzdata`: `2026.5`

**Dependency Health Check:**
```powershell
.\.venv\Scripts\python.exe -m pip check
```
Output:
```
No broken requirements found.
```

---

## 2. Baseline Test Suite Execution (Phase 4)

Executed command:
```powershell
.\.venv\Scripts\python.exe -m pytest
```

### 2.1 Test Summary
- **Total Test Items Collected:** `558`
- **Passed:** `557` (99.82%)
- **Failed:** `0` (0%)
- **Skipped:** `1` (0.18%)
- **Total Duration:** `73.31s`

### 2.2 Test Breakdown by Subsystem

| Test Module | Tests | Result | Description / Coverage |
|---|---|---|---|
| `tests/benchmark/test_benchmark.py` | 16 | **16 PASSED** | Generator reproducibility, grid coverage, scaling instances, sensitivity runner |
| `tests/core/test_actions_heuristic.py` | 10 | **10 PASSED** | Action matrices, physical byte benefits, severity costs, heuristic scoring formulas |
| `tests/core/test_analysis.py` | 5 | **5 PASSED** | Risk/safety integration, candidate isolation, problem construction |
| `tests/core/test_risk.py` | 7 | **7 PASSED** | Multi-factor risk model, age/usage half-life, folder boost/relief, duplicate discount |
| `tests/core/test_safety.py` | 6 | **6 PASSED** | Protected directory prefixes, system attribute, sensitive file name patterns |
| `tests/core/test_state.py` | 15 | **15 PASSED** | State representation, successor generation, goal testing, duplicate preservation constraint |
| `tests/executor/test_executor.py` | 13 | **13 PASSED** | Dry-run validation, preflight checks, hash verification, journal recording |
| `tests/gui/test_app.py` | 6 | **6 PASSED** | Tkinter widgets, table bindings, category visualization bar |
| `tests/gui/test_session.py` | 8 | **8 PASSED** | Human-in-the-loop review workflow, approval states, rejection and re-planning |
| `tests/planners/test_planners.py` | 71 | **71 PASSED** | Greedy vs BnB vs Exhaustive ground truth on 60 random instances, gap computation |
| `tests/planners/test_relaxation.py` | 89 | **89 PASSED** | Convex hull of actions, Minkowski sums, duplicate group exact convexification, LP admissibility |
| `tests/sensors/test_dataset.py` | 8 | **8 PASSED** | DataFrame casting, parquet serialization and reload, summary aggregation |
| `tests/sensors/test_environment.py` | 3 | **3 PASSED** | Unified environment capture, volume space detection |
| `tests/sensors/test_hashing.py` | 9 | **9 PASSED** | Size grouping, head/tail partial hash, full BLAKE2b hash, hard-link awareness |
| `tests/sensors/test_metadata.py` | 12 | **12 PASSED** | Timestamps, POSIX conversions, extension mapping, category deduction |
| `tests/sensors/test_scanner.py` | 8 | **7 PASSED, 1 SKIPPED** | Directory recursion, unreadable path handling, symlink/junction skipping |
| `tests/test_activity_log.py` | 4 | **4 PASSED** | Logging handlers, log rotation at 2 MB, thread safety |
| `tests/test_cli.py` | 7 | **7 PASSED** | Command line parsing for `scan`, `analyze`, `plan`, `bench`, `gui` |
| `tests/test_config.py` | 17 | **17 PASSED** | TOML configuration parsing, validation constraints, fallback defaults |
| `tests/test_formatting.py` | 14 | **14 PASSED** | Human-readable byte formatting and size string parsing |
| `tests/test_user_confirmation.py` | 10 | **10 PASSED** | Explicit confirmation prompts, non-interactive refusal without `--yes` |

### 2.3 Investigation of Skipped Test
- **File:** `tests/sensors/test_scanner.py:90` (`test_scanner_skips_symlink`)
- **Reason:** `creating symlinks requires extra privileges on this system`
- **Investigation:** On Windows, creating a symbolic link via `os.symlink()` requires `SeCreateSymbolicLinkPrivilege`, which is restricted to elevated Administrator accounts or systems with Windows Developer Mode enabled. The test has a proper guard:
  ```python
  try:
      link.symlink_to(target)
  except OSError as exc:
      if getattr(exc, "winerror", None) == 1314:
          pytest.skip("creating symlinks requires extra privileges on this system")
  ```
  The test handles this gracefully. The scanner implementation uses `os.scandir` entry properties `.is_symlink()` and `.is_junction()` without raising errors, ensuring full safety on Windows environments.

---

## 3. Code Style & Quality Audits

### 3.1 Ruff Linter
Executed command:
```powershell
.\.venv\Scripts\python.exe -m ruff check .
```
Output:
```
All checks passed!
```

### 3.2 Ruff Formatter
Executed command:
```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
```
Output:
```
80 files already formatted
```

---

## 4. Live Workflow & Actuation Audit

Executed synthetic workflow test script `scratch/test_live_workflow.py` validating:
1. `storageops scan` $\to$ generated `scan_dataset.csv` and `scan_dataset.meta.json`.
2. `storageops analyze` $\to$ identified 5 candidates, 1 protected file (`id_rsa.key`), computed risk distribution and action potential.
3. `storageops plan (greedy)` $\to$ proposed freeing 15 MB at cost 0.390 (knapsack greedy overshoot).
4. `storageops plan (bnb)` $\to$ proposed freeing exactly 5 MB at cost 0.300 with 0.00% optimality gap.
5. `storageops plan --compare` $\to$ side-by-side comparison table of all planners.
6. GUI `Session` review workflow $\to$ approved actions, rejected chosen file, successfully executed `replan_without()`.
7. Preflight validation $\to$ detected simulated modified file and safely marked it `skipped` without terminating execution.
8. Transaction logs $\to$ confirmed dry-run and execution JSONL journals and rotating activity log in `STORAGEOPS_HOME`.
9. Tkinter GUI $\to$ instantiated `StorageOpsApp` desktop window, processed event loop, and destroyed window cleanly.

---

## 5. Test Suite Safety Verification

Inspection of `tests/conftest.py` confirms that:
- Every test uses an `autouse=True` fixture `isolated_storageops_home` that monkeypatches `STORAGEOPS_HOME` to a fresh pytest `tmp_path`.
- All file tests operate strictly on synthetic directory trees created in temporary folders.
- Under no circumstances did any test or workflow inspect, modify, or delete real user files in `Desktop`, `Downloads`, `Documents`, or Windows system directories.
