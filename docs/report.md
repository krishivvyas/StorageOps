# StorageOps: AI-Based Intelligent Storage Space Optimization and Safe Cleanup Planner

**Project-Based Learning Report**
ECSCT24301 – Foundation of AI · Applied AI Project Track · Division C

| Team member | URN |
|---|---|
| Het Patel | 711 |
| Krishiv Vyas | 186 |
| Parth Patel | 113 |

---

## Abstract

Computers lose free space to large unused files, duplicates, installers, caches and other
low-value data. Conventional storage cleaners apply fixed rules ("delete temporary files")
that ignore how much space the user actually needs, how important each file is, and what
the safest way to obtain that space is.

**StorageOps** formulates storage cleanup as a formal AI planning and optimization problem.
A read-only sensor layer scans a folder and builds a metadata dataset: sizes, ages,
categories and content-hash duplicates. A risk model estimates each file's importance, hard
safety constraints protect system and sensitive files, and a cost function combines risk with
a penalty for unnecessary actions. For every eligible file the agent chooses one of five
actions (KEEP, DELETE, ARCHIVE, COMPRESS, MOVE) so that a user-defined free-space target is
reached at minimum total cost.

The problem is a minimisation form of the multiple-choice knapsack problem, which is NP-hard.
We implement a greedy baseline and a branch-and-bound planner with an LP-relaxation lower
bound. The bound is strengthened by an exact convex hull for duplicate groups, a weakness we
discovered through benchmarking.

On 108 synthetic benchmark instances with up to 1,500 files:
- branch-and-bound found and proved the optimal plan on every feasible instance, in at most
  3.8 s, and it scales roughly linearly to 2,560 files;
- the greedy baseline averaged 2–11% above optimal and, on small instances, up to 220% above.

An interactive tkinter desktop application implements a review-and-approve workflow. Every
step requires the user's explicit OK, deletions go to the Recycle Bin, archives and copies
are verified before any original is removed, and every action is logged.

---

## 1. Introduction

### 1.1 Problem statement

Laptop and desktop storage fills up with large unused files, duplicate copies, old
installers, temporary files and caches. A typical cleaner deletes files by fixed rules, but
fixed rules cannot answer the questions that matter to a user:

- *How much* space do I need, and what is the least disruptive way to get exactly that much?
- Which files are genuinely unimportant, given their type, age, location and whether a copy
  exists elsewhere?
- Is it better to delete a file, archive it, compress it in place, or move it to another
  drive?
- What happens if the requested amount of space cannot be freed safely?

### 1.2 Objectives

1. Model storage cleanup as a formal AI problem: a state space, an action set, a goal test
   and a path cost.
2. Build a sensor layer that collects reliable file metadata and detects duplicates
   efficiently.
3. Design an explainable risk model and a heuristic for prioritising cleanup actions.
4. Implement a baseline (greedy) and an advanced (branch-and-bound) planner, and compare them
   quantitatively.
5. Analyse failure cases: infeasible targets, large search spaces, and sensitivity to the
   heuristic weights.
6. Deliver an interactive prototype in which no file is touched without the user's explicit
   approval.

### 1.3 Contributions

- A complete formulation of safe storage cleanup as a constrained **multiple-choice knapsack
  problem**, with five actions, a decomposable cost function and a duplicate-copy constraint.
- A **group-aware LP relaxation**: the exact convexification of "never delete every copy of a
  duplicate". It turned branch-and-bound time-outs with 45–66% optimality gaps into proven
  optimal solutions, with the 10%-duplicate instance needing about 35× fewer nodes.
- A **reproducible benchmark** with a synthetic instance generator, a scaling study and a
  heuristic-weight sensitivity study.
- A **safety-first prototype**: mandatory confirmation at every step, mandatory dry runs,
  verification before removal, a Recycle Bin for deletions, an audit journal and an activity
  log.

---

## 2. Agent and environment

### 2.1 PEAS description

| PEAS element | Specification |
|---|---|
| **Performance measure** | Free-space target achieved; total plan cost (risk); number of unnecessary actions; planning time; solution quality (gap to the optimum) |
| **Environment** | A user-selected local folder, or a synthetic benchmark instance: files with size, type, age, location, attributes and duplicate status, plus the volume's free space |
| **Actuators** | Per-file actions KEEP, DELETE, ARCHIVE, COMPRESS, MOVE, carried out by the executor only after explicit approval |
| **Sensors** | File-system scanner and metadata collector (path, size, timestamps, attributes, category, content hash), and the volume free-space reader |

### 2.2 Environment properties

| Property | Value | Reason |
|---|---|---|
| Observability | Partially observable | The true importance of a file to the user cannot be sensed; it is estimated by the risk model |
| Determinism | Deterministic (for planning) | The effect of each action on free space is computed from the action model |
| Episodic / sequential | Sequential | Decisions interact through the space target, the duplicate constraint and the budget |
| Static / dynamic | Static during planning | Files can change between scan and execution, so the executor re-checks every file before acting |
| Discrete / continuous | Discrete | Finite files × five actions |
| Agents | Single agent | |

---

## 3. Formal problem formulation

### 3.1 Actions

| Action | Effect | Benefit b(f, a): bytes freed on the source volume |
|---|---|---|
| KEEP | No change | 0 |
| DELETE | Send to the Recycle Bin (recoverable) | s |
| ARCHIVE | Compress into an archive, verify it, then remove the original | s · (1 − r) |
| COMPRESS | Transparent NTFS compression in place; the file stays usable | s · (1 − r) · e |
| MOVE | Relocate to another drive without compression | s (only if the destination is on another volume) |

Here s is the file size, r the expected compression ratio of the file's category (≈1.0 for
video and zip files, 0.15 for logs), and e = 0.6 the efficiency of in-place compression
relative to a real archiver. ARCHIVE therefore frees more than COMPRESS but is more
disruptive, which gives the planner a genuine trade-off.

### 3.2 State-space formulation

Let f₀ … f₍ₙ₋₁₎ be the candidate files in a fixed decision order, F₀ the current free space,
T the target free space, and R = max(0, T − F₀) the number of bytes the plan must free.

| Component | Definition |
|---|---|
| **State** | A partial plan (a₀, …, a₍ₖ₋₁₎): actions chosen for the first k files. Every undecided file is implicitly KEPT, so every state is also a complete, executable plan. The state also stores the bytes freed Bₖ, the path cost Cₖ, and the number of DELETEs in each duplicate group. |
| **Initial state** | k = 0, nothing assigned, B = 0, C = 0 |
| **Actions / successors** | Assign one allowed action to fₖ. Children that would delete the last surviving copy of a duplicate group, or exceed the risk budget, are not generated. |
| **Goal test** | Bₖ ≥ R and Cₖ ≤ C_max |
| **Path cost** | Cₖ = Σ c(fᵢ, aᵢ) |
| **State-space size** | ∏ᵢ \|A(fᵢ)\| complete plans, up to 5ⁿ (about 10⁴⁹⁵⁹ for the 7,694 candidates of one real scan) |

### 3.3 Optimisation problem

```
minimise    Σᵢ c(fᵢ, aᵢ)
subject to  Σᵢ b(fᵢ, aᵢ) ≥ R                                (free-space target)
            aᵢ ∈ A(fᵢ)                                      (safety: allowed actions only)
            for every duplicate group: not every copy is DELETEd
            Σᵢ c(fᵢ, aᵢ) ≤ C_max                             (optional risk budget)
```

Each file is a "class" from which exactly one option is chosen, and the options must jointly
cover a capacity. This is the minimisation (covering) form of the **multiple-choice knapsack
problem**, which is NP-hard [3]. This justifies comparing a fast greedy method with an exact
search.

---

## 4. System design

### 4.1 Pipeline

```
 ┌──────────────┐   ┌─────────────────────┐   ┌──────────────────┐   ┌──────────────────┐
 │ 1. Sensors   │──▶│ 2. Analysis         │──▶│ 3. Planners      │──▶│ 5. Review & act  │
 │ scan, hash,  │   │ risk · safety ·     │   │ greedy ·         │   │ approve · dry    │
 │ disk usage   │   │ actions · heuristic │   │ branch & bound   │   │ run · execute    │
 └──────────────┘   │ → CleanupProblem    │   └──────────────────┘   └──────────────────┘
   read-only        └─────────────────────┘     proposal only          user OK at every step
                               ▲
                    ┌──────────┴──────────┐
                    │ 4. Benchmark:       │
                    │ synthetic instances │
                    └─────────────────────┘
```

Only the executor (stage 5) can modify files. Sensing, analysis and planning are read-only,
and the planners never import the executor.

### 4.2 Implementation

Python 3.12. Dependencies: pandas (datasets), matplotlib (figures), send2trash (Recycle
Bin); tkinter for the GUI. About 6,200 lines of source code and 2,400 lines of tests.

| Package | Lines | Responsibility |
|---|---|---|
| `sensors/` | 769 | Scanner, metadata, staged duplicate detection, datasets, disk usage |
| `core/` | 744 | Risk model, safety constraints, action model, heuristic, state space |
| `planners/` | 766 | Greedy, branch-and-bound, LP relaxation, exhaustive reference |
| `benchmark/` | 988 | Instance generator, runner, sensitivity study, figures, report |
| `executor/` | 453 | Safe file operations, journal |
| `gui/` | 1,341 | Workflow session (toolkit-independent), tkinter app, widgets |
| top level | 1,106 | Shared models, configuration, CLI, activity log |

All tunable parameters (risk weights, action costs, heuristic weights, protected paths) live in
a validated TOML configuration, `config/default.toml`.

---

## 5. Methodology

### 5.1 Sensors (Phase 1)

**Scanner.**
- Walks the folder iteratively with `os.scandir`, so there is no recursion limit.
- Does not follow symbolic links or NTFS junctions, which avoids cycles and double counting.
- Skips cloud-only placeholder files, which use no local space and would otherwise be
  downloaded.
- Records unreadable folders as issues instead of failing.

**Metadata and categories.** Each file gets its size, timestamps, age, idle time, attributes
(hidden, system, read-only) and one of 14 categories. The category comes from the extension
or from folder hints such as Temp, Cache or Logs. The hints are taken only from the scanned
folder downwards; an early bug labelled every file under a `…\Temp\…` project folder as
temporary.

**Staged duplicate detection.** Hashing every file is expensive, so candidates are narrowed
step by step:
1. group by exact size;
2. compare a partial BLAKE2b hash of the first and last 64 KB;
3. compute a full content hash only where partial hashes still collide.

Hard links to the same physical file are not counted as reclaimable space. On a real scan of
7,887 files, only 11 MB were read to confirm 191 duplicate groups.

### 5.2 Risk model and safety constraints (Phase 2)

The **risk** R(f) ∈ [0, 1] estimates the value lost if a file were removed:

```
base(f) = 0.60 · category_base(f) + 0.25 · recency(f) + 0.15 · usage(f)
raw(f)  = clip(base(f) + location(f) + flags(f), 0, 1)
R(f)    = raw(f) · (0.25 if f has an identical copy, else 1)

recency = 0.5^(age_days / 90)        usage = 0.5^(idle_days / 90)
location: +0.20 under Documents/Desktop/Pictures/…, −0.15 under Downloads/Temp/Cache/…
flags:    +0.05 hidden, +0.10 read-only
```

The category base ranges from 0.05 (temporary, cache) to 1.0 (system). The duplicate
discount is sound only because the state space forbids deleting every copy, so a duplicate's
content always survives somewhere.

**Hard safety constraints.** These files may only be KEPT:
- files under the Windows, Program Files or ProgramData folders;
- files with the system attribute or in the system category;
- files whose names match sensitive patterns: private keys, certificates, password vaults.

**Risk threshold.** An action is allowed only if severity(a) · R(f) ≤ 0.6. A fresh document
(R = 0.9) can therefore be archived (0.36) but not deleted (0.9).

### 5.3 Cost function

```
c(f, KEEP) = 0
c(f, a)    = severity(a) · R(f) + penalty(a)      (+∞ if a is not allowed for f)
severity:  DELETE 1.0 · ARCHIVE 0.4 · MOVE 0.25 · COMPRESS 0.1
penalty:   0.02–0.04 per touched file
```

The per-file penalty implements the proposal's "unnecessary actions" term. Between two plans
that free the same space, the one that touches fewer files is cheaper.

### 5.4 Heuristic

```
h(f, a) = 0.35 · B(f, a) + 0.15 · I(f) + 0.15 · D(f) − 0.35 · severity(a) · R(f)
B = log(1 + benefit) / log(1 + max benefit),   I = 1 − 0.5^(age/180),   D = duplicate flag
```

**Design decision: the heuristic guides, the cost function judges.** h is used to order
choices and to explain them, but optimality is always measured by the cost function. The
weight experiments in Section 7.4 can therefore change search behaviour and greedy quality,
but never the definition of "optimal".

### 5.5 Planners (Phase 3)

**Greedy baseline: the multiple-choice knapsack greedy.**
- Each file's allowed (benefit, cost) points form a lower convex hull through (0, 0), e.g.
  KEEP → COMPRESS → DELETE.
- Every hull edge is an "upgrade" with an incremental cost per byte. Greedy takes edges from
  all files in increasing cost-per-byte order until the target is met, skipping edges that
  break a constraint. This implements the proposal's "low-risk, high-yield first" rule.
- Redundant actions are then removed, costliest first.
- A second variant ranks by the heuristic h instead (used in the sensitivity study).

**Branch-and-bound.** A depth-first search over the state space of Section 3.2, with
backtracking on mutable arrays (O(n) memory):

| Ingredient | Design |
|---|---|
| Initial upper bound | The better of the two greedy plans |
| Lower bound | g + LP relaxation of the remaining units: hull edges filled in cost-per-byte order, the last one fractionally [2] |
| Duplicate groups | Bounded as one unit. Their hull is the exact convexification of "every copy but one may be deleted": the lower hull of the union, over the kept copy m, of the Minkowski sums of the members' hulls (with DELETE removed for m) |
| Goal cut-off | A node that meets the target is recorded and not expanded, because keeping every remaining file is its cheapest completion |
| Pruning | A child is pruned if g ≥ U, g > C_max, g + LP ≥ U, or it would delete a group's last copy |
| Ordering | Files: cheapest bytes first, with duplicate groups kept contiguous. Actions: lowest g + LP first (or highest h first) |
| Limits | Optional time or node limit. It returns the best plan found and a certified gap (cost − root LP) / cost |

The relaxation drops only integrality and the risk budget, so it never overestimates and no
optimal plan is ever pruned.

**Exhaustive search.** Plain enumeration through the public `successors()` interface (up to 16
files). It serves as ground truth for tests and benchmarks.

---

## 6. Benchmark and experimental setup (Phase 4)

**Instance generator.** Synthetic file tables with the same columns as a real scan, so they go
through the identical pipeline:
- categories drawn from a risk-level mix;
- lognormal sizes around per-category medians (video ≈ 700 MB, source files ≈ 20 KB);
- typical folders for each category;
- lognormal ages;
- a share of exact duplicate copies;
- 2% protected system files.

Generation is deterministic: each instance is seeded from a hash of its full name.

| Study | Design |
|---|---|
| Main grid | 3 size classes (10 / 150 / 1,500 files) × duplicates {0, 0.3} × risk {low, mixed, high} × target {10%, 30% of unprotected bytes} × 3 seeds = **108 instances** |
| Scaling | 10 → 2,560 files (doubling), mixed risk, 3 seeds each |
| Sensitivity | 6 medium instances; each heuristic weight swept over {0, 0.25, 0.5, 0.75, 1} plus its default |

**Quality metric.** Excess cost = plan cost / cheapest goal plan found on the instance − 1.
Branch-and-bound proved optimality on all feasible instances, so this is excess over the true
optimum.

**Settings.**
- MOVE is disabled in the default benchmark. With a second drive available, nearly every
  instance is solved by moving one large video, which cannot tell the planners apart.
- Branch-and-bound has a 5 s time limit per instance.
- Machine: Windows 11, single thread.

Full tables: [benchmark_results.md](benchmark_results.md).

---

## 7. Results

### 7.1 Solution quality

![Solution quality by size class](figures/quality_by_size.png)

| Planner | Plans equal to optimum | Mean excess: small / medium / large | Worst excess |
|---|---|---|---|
| Greedy (ratio) | 57% | 11.1% / 7.9% / 2.0% | 220% |
| Greedy (heuristic) | 35% | 43.4% / 44.5% / 256.6% | 1,157% |
| **Branch & bound** | **100%** (all proven) | **0 / 0 / 0** | **0** |
| Exhaustive (small only) | 100% | 0 | 0 |

### 7.2 Speed and scaling

![Planning time vs. instance size](figures/scaling_time.png)

| Files | Greedy (ratio) | Branch & bound | Exhaustive |
|---|---|---|---|
| 10 | 0.10 ms | 0.94 ms | 114 ms |
| 160 | 0.89 ms | 5.9 ms | not feasible |
| 640 | 3.2 ms | 26 ms | not feasible |
| 2,560 | 15 ms | 114 ms | not feasible |

Median times. Branch-and-bound grows roughly linearly with instance size on these instances
and never hit its time limit in the scaling study. Its maximum on the 1,500-file main-grid
instances was 3.8 s.

### 7.3 Search effort

![Nodes generated vs. instance size](figures/scaling_nodes.png)

On 10-file instances, branch-and-bound generates a median of **4 nodes** where exhaustive search
generates **50,184**: a reduction of about 12,500× from the LP bound and the greedy starting
plan. At 1,280 files it needs a median of 1,728 nodes.

### 7.4 Heuristic-weight sensitivity

![Weights vs. greedy (heuristic) plan quality](figures/sensitivity_quality.png)

| Weight | Effect on heuristic-greedy quality |
|---|---|
| Benefit | Higher is better: excess falls from 132% (w = 0) to 61% (w = 1) |
| Inactivity | Narrow sweet spot: 80% at 0.25, but 195% at 0.75 |
| Duplicate | Best at 0 (35%); every positive value is worse (95–143%) |
| Risk | Best at 0 (41%): risk already appears in the cost, so adding it to h double-counts it |

No weight setting makes heuristic greedy competitive with ratio greedy (its best is 34–35%
excess vs about 8% on comparable instances). The heuristic ranks the *desirability of touching
a file*, not the *cost per byte*. Branch-and-bound with heuristic ordering generated the same
~127 nodes for every weight setting: with a good starting plan and a tight bound, ordering
hardly matters.

---

## 8. Error and failure analysis

Full analysis: [failure_analysis.md](failure_analysis.md).

| # | Failure mode | Evidence | Cause and response |
|---|---|---|---|
| 1 | **Greedy overshoot** | Small instance: greedy cost 0.42 vs optimum 0.13 (+220%), freeing 6.4× the required space | The cheapest bytes sat in one large file. Classic knapsack-greedy weakness; branch-and-bound avoids it |
| 2 | **Heuristic as objective** | Heuristic greedy 35–257% above optimal | h measures desirability per file, not per byte. h is used only for ordering and explanation |
| 3 | **Weak bound with duplicates** (found and fixed) | Before the fix, branch-and-bound timed out with 45–66% gaps on duplicate-rich instances | The relaxation ignored "keep one copy" and "deleted" every cheap duplicate. The exact group hull now solves them optimally in 0.1–0.8 s |
| 4 | **Infeasible targets** | 25 of 108 instances; 61% of high-risk ones | In high-risk environments, deletion breaks the safety threshold. Correctly reported, never forced. Detected in milliseconds at the root |
| 5 | **Time limits** | 1 of 270 runs (h-ordered branch-and-bound): 0.31% above optimal, certified gap 0.94% | Many near-identical cheap options. The bound-ordered default solved the same instance in 3.8 s |
| 6 | **MOVE trivialises the problem** | All probe configurations optimal in < 5,000 nodes | Moving to another drive is cheap and frees whole files. Benchmarked without MOVE |
| 7 | **Methodology bug** (found and fixed) | All seeds of a size class were identical | Seeds came from only the first 8 bytes of the instance name. Now hashed from the full name, with a regression test |

**Threats to validity.**
- The data is synthetic and the risk weights are hand-set, so "optimal" means optimal under
  this cost model.
- Timings are single-machine.
- Exhaustive ground truth only reaches about 12 files. Larger instances rely on
  branch-and-bound's optimality proof, which is itself checked against exhaustive search on
  120 random instances in the test suite.

---

## 9. Interactive prototype (Phase 5)

### 9.1 Review-and-approve workflow

`storageops gui` opens a tkinter desktop application with four tabs:

| Tab | Content |
|---|---|
| Overview | Volume usage, duplicate summary, protected-file count, and space by category (table and bar chart with hover details) |
| Files & risk | Every file with its category, size, age, risk score, protection reason and best action; filters by category, protection and path |
| Plan review | Proposed actions with what each one does, bytes freed, risk and cost; an approve checkbox per row; approve all/none; *reject selected & re-plan* |
| Results | The outcome of the dry run or execution per file, plus the journal and log locations |

Workflow:
1. **Load:** scan a folder (read-only) or open a saved dataset.
2. **Plan:** set the target ("free an extra 5 GB" or "reach 100 GB free"), optionally a MOVE
   destination, and a planner.
3. **Review:** every proposed action starts **unapproved**. The user approves rows, or
   rejects files and asks for an alternative plan that keeps them.
4. **Dry run:** required, for exactly the current selection. It checks every file and changes
   nothing.
5. **Execute:** a final confirmation lists the operations and the expected space freed.

### 9.2 Safety layers

| Layer | Guarantee |
|---|---|
| User's OK | Every step (scan, plan, re-plan, dry run, execute) needs explicit confirmation. The workflow core refuses any step without it, in the GUI (dialogs) and the CLI (`[y/N]` or an explicit `--yes`) alike |
| Approval | Only approved actions can run, and only after a dry run of that exact selection |
| Preflight | A file is skipped if it is missing, changed since the scan, under a protected location, or a system file |
| DELETE | Recycle Bin only, so it can always be undone |
| ARCHIVE / MOVE | The original is removed only after the archive or copy has been re-read and its content hash matches |
| COMPRESS | Native NTFS compression; the content hash is checked to be unchanged |
| Isolation | A failure on one file never affects the others; Cancel stops before the next file |
| Audit | Activity log of every step and decision, plus a per-run JSON journal recording where each file went (bin, archive path, destination) |

---

## 10. Testing and validation

The suite has 558 automated tests: **557 pass, 1 is skipped**. The skipped test needs
symbolic-link privileges on Windows. Lint and formatting are clean. Details:
[test_results.md](test_results.md).

| Area | Tests | Highlights |
|---|---|---|
| Sensors | 41 | Duplicate detection including middle-of-file differences, hard links, and files that vanish during scanning |
| Analysis & state space | 43 | Risk formula against hand-computed values; `successors()` checked against brute-force enumeration |
| Planners | 379 | Branch-and-bound matches exhaustive search on 120 random instances under every ordering; the LP bound never exceeds the optimum |
| Benchmark | 16 | Determinism, independent seeds, duplicate conventions |
| Executor & GUI | 41 | Verify-before-remove, refusal of protected files, mandatory dry run and confirmation, full workflow in the real (hidden) window |
| Shared | 38 | Configuration validation, CLI, size parsing |

All tests use synthetic files in temporary folders, a fake Recycle Bin and an isolated log
folder, so the test suite can never touch user data.

---

## 11. Scope, limitations and ethics

- **Scope.** StorageOps is an academic planning prototype, not a replacement for operating
  system maintenance tools. Its contribution is the AI formulation, the search and
  optimisation methods, the heuristic analysis, the benchmarking, and explainable decisions.
- **Risk is estimated, not known.** A file's true importance is unobservable. The model is
  conservative: protected categories, a risk threshold, and duplicates discounted only when a
  copy is guaranteed to survive. Even so, the user remains the final authority on every
  action.
- **Platform.** In-place compression and some protections are Windows/NTFS-specific. The
  planners and analysis are platform-independent.
- **Recycle Bin.** Deleted files free space only once the bin is emptied; this is deliberate,
  so every deletion is reversible.
- **Ethics and safety.** The design follows "propose, don't act": no destructive action is
  automatic, every step requires consent, and everything is logged.

---

## 12. Conclusion and future work

StorageOps shows that safe storage cleanup is naturally an optimisation problem: a
constrained multiple-choice knapsack. Treating it that way gives better answers than fixed
rules:

- it frees exactly the space requested, at minimum estimated risk;
- it explains why each file was chosen;
- it reports clearly when a target cannot be met safely.

Branch-and-bound with a group-aware LP bound solves realistic instances of thousands of files
to proven optimality in well under a second, typically. The greedy baseline is a good fast
fallback on large instances but fails on small ones. Benchmarking also exposed and fixed a
real weakness in the bound, which is exactly what the evaluation phase is for.

**Future work.**
- Learn the risk weights from user approve/reject decisions (preference learning), instead
  of hand-setting them.
- One-click undo from the journal (move back, extract archive, decompress).
- A "single cheapest file that alone meets the target" candidate for the greedy baseline,
  which removes its worst-case overshoot.
- Incremental re-scanning, and Linux/macOS support for in-place compression.

---

## References

1. S. Russell and P. Norvig, *Artificial Intelligence: A Modern Approach*, 4th ed., Pearson,
   2020. Problem formulation, state-space search, heuristics.
2. P. Sinha and A. A. Zoltners, "The multiple-choice knapsack problem," *Operations Research*,
   27(3), 503–515, 1979. LP relaxation via convex hulls.
3. H. Kellerer, U. Pferschy and D. Pisinger, *Knapsack Problems*, Springer, 2004. Complexity
   and algorithms for knapsack variants.
4. A. H. Land and A. G. Doig, "An automatic method of solving discrete programming problems,"
   *Econometrica*, 28(3), 497–520, 1960. Branch-and-bound.
5. Python Software Foundation, *Python 3.12 documentation*: `os.scandir`, `hashlib` (BLAKE2),
   `zipfile`, `tkinter`.
6. The pandas development team, *pandas*; J. D. Hunter et al., *Matplotlib*.

---

## Appendix A: How to run

```bash
cd storageops
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,parquet]"

storageops scan "C:\Users\me\Downloads" -o data/downloads.csv   # read-only scan
storageops analyze data/downloads.csv --free 5GB                # risk & feasibility
storageops plan data/downloads.csv --free 5GB --compare         # greedy vs. branch & bound
storageops gui                                                  # review-and-approve app
python scripts/run_benchmarks.py                                # benchmark (~30 s)
python -m pytest                                                # 558 tests (~20 s)
```

Every command asks for confirmation before doing anything; `--yes` confirms up front.
Logs and journals are written to `~/.storageops` (or to `STORAGEOPS_HOME`).

## Appendix B: Main parameters (defaults)

| Parameter | Value |
|---|---|
| Risk weights (category / recency / usage) | 0.60 / 0.25 / 0.15 |
| Recency and usage half-life | 90 days |
| Duplicate risk factor | 0.25 |
| Action severity (DELETE / ARCHIVE / MOVE / COMPRESS) | 1.0 / 0.4 / 0.25 / 0.1 |
| Max allowed severity × risk | 0.6 |
| In-place compression efficiency | 0.6 |
| Heuristic weights (benefit / inactivity / duplicate / risk) | 0.35 / 0.15 / 0.15 / 0.35 |
| Branch-and-bound time limit (GUI/CLI default) | 10 s |

## Appendix C: Team contributions

*To be completed by the team.*

| Member | Contributions |
|---|---|
| Het Patel | |
| Krishiv Vyas | |
| Parth Patel | |
