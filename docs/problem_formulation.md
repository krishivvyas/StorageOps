# StorageOps: Problem Formulation

## PEAS

| Element | Specification |
|---|---|
| **Performance** | Free-space target achieved; plan cost/risk; number of unnecessary actions; planning time; solution quality (gap to optimal). |
| **Environment** | A selected local storage location, or a synthetic benchmark instance: files with size, type, age, duplicate status and usage information. Partially observable (true importance is unknown), static during planning, discrete, single-agent. |
| **Actuators** | Per-file actions KEEP, DELETE, ARCHIVE, COMPRESS, MOVE, carried out only after explicit user approval. |
| **Sensors** | File-system scanner and metadata collector (`storageops.sensors`): path, size, type, timestamps, attributes, content hash, volume free space. |

## Actions

| Action | Effect | Benefit b(f, a): bytes freed on the source volume | Severity |
|---|---|---|---|
| KEEP | No change | 0 | 0 |
| DELETE | Send to Recycle Bin (recoverable) | s | 1.0 |
| ARCHIVE | Compress into an archive, verify the archive, remove the original | s * (1 - r) | 0.4 |
| COMPRESS | Transparent in-place compression; file stays usable at its path | s * (1 - r) * e | 0.1 |
| MOVE | Relocate without compression | s, only to a destination on another volume | 0.25 |

s = file size; r = expected compression ratio for the file's category; e = 0.6, the
efficiency of in-place compression relative to an archiver. Because e < 1, ARCHIVE frees
more space than COMPRESS but is more disruptive: a real trade-off for the planner. All values
are configurable in `config/default.toml`.

## Risk model: R(f) in [0, 1] (`core/risk.py`)

Estimated value lost if f were removed.

```
base(f) = 0.60 * category_base(f) + 0.25 * recency(f) + 0.15 * usage(f)
raw(f)  = clip(base(f) + location(f) + flags(f), 0, 1)
R(f)    = raw(f) * (0.25 if f has an identical copy else 1)

recency(f)  = 0.5 ^ (age_days / 90)          1 = modified today
usage(f)    = 0.5 ^ (idle_days / 90)         1 = opened today (weak signal on Windows)
location(f) = +0.20 under Documents/Desktop/Pictures/...,  -0.15 under Downloads/Temp/Cache/...
flags(f)    = +0.05 hidden, +0.10 read-only
```

category_base ranges from 0.05 (temporary, cache) through 0.5 (video, other) and 0.8
(documents, source code) up to 1.0 (system).

**Why the duplicate discount is sound:** the state space forbids deleting every copy of a
duplicate group, so a duplicate's content always survives. Without that constraint the cost
function would not decompose per file.

## Safety constraints (`core/safety.py`)

**Hard constraints.** A file is protected, and may only be KEPT, if:
- it lies under %SystemRoot%, %ProgramFiles%, %ProgramFiles(x86)% or %ProgramData%;
- it has the Windows "system" attribute;
- its category is protected (system by default); or
- its name matches a sensitive pattern (`*.pem`, `*.key`, `*.kdbx`, `id_rsa*`, ...).

**Risk threshold.** Action a is allowed for f only if severity(a) * R(f) <= 0.6. So a fresh
document with R = 0.9 cannot be DELETEd (0.9) but may be ARCHIVEd (0.36). Also:
- ARCHIVE / COMPRESS require a saving of at least 10% (no re-compressing video or zip files);
- MOVE requires a destination on another volume;
- every action must free at least 1 byte.

## Cost function: the objective (`core/actions.py`)

```
c(f, KEEP) = 0
c(f, a)    = severity(a) * R(f) + penalty(a)        (+inf if a is not allowed for f)
```

The flat penalty (0.02 to 0.04 per touched file) encodes "unnecessary actions". Among plans
that free the same space, the one touching fewer files is cheaper, which favours a few large
files over many small ones.

## Heuristic: search guidance (`core/heuristic.py`)

```
h(f, a) = 0.35 * B(f, a) + 0.15 * I(f) + 0.15 * D(f) - 0.35 * severity(a) * R(f)
B = log(1 + benefit) / log(1 + max benefit)   I = 1 - 0.5 ^ (age_days / 180)   D = duplicate flag
h(f, KEEP) = 0,   h = -inf if not allowed
```

**Design decision:** h only orders choices (greedy selection order, branch-and-bound value
ordering), while optimality is always measured by the cost function. The proposal's
heuristic-weight experiments (Phase 4) therefore change search behaviour and greedy plan
quality, but never what "optimal" means, so the comparison stays fair.

## State space (`core/state.py`)

| Component | Definition |
|---|---|
| Instance | Candidate files f_0..f_(n-1) in a fixed decision order (default: largest first). Protected files and files with no allowed action are fixed context, not variables. Free space F0, target T, required R = max(0, T - F0), optional risk budget C_max. |
| State | Partial plan (a_0, ..., a_(k-1)), plus B_k = sum b, C_k = sum c, and DELETE counts per duplicate group. Undecided files are implicitly KEPT, so every state is an executable plan. |
| Initial state | k = 0, B = 0, C = 0 |
| Successors | Assign each allowed action to f_k, except where it would DELETE the last copy of a duplicate group, or where C_k + c > C_max. Costs are >= 0, so this pruning never removes a goal. |
| Goal test | B_k >= R and C_k <= C_max |
| Path cost | C_k |
| Size | prod over i of \|A(f_i)\| complete assignments, up to 5^n |

The task is to find a goal state of minimum path cost:

```
minimise    sum_i c(f_i, a_i)
subject to  sum_i b(f_i, a_i) >= R
            a_i in A(f_i)                              (safety)
            not every copy of a duplicate group is deleted
            sum_i c(f_i, a_i) <= C_max
```

This is a minimisation form of the **multiple-choice knapsack problem** with side
constraints, which is NP-hard. That motivates comparing a fast heuristic method with an
exact search.

## Planners (`planners/`)

All planners take a `CleanupProblem` and return a `PlannerResult`: the plan, its status
(`optimal` / `feasible` / `infeasible` / `no plan found`), search statistics, and a lower
bound for reporting the optimality gap.

### Greedy baseline (`greedy.py`)

- **`ratio` key (default): the multiple-choice knapsack greedy.** Each file's allowed
  (benefit, cost) points form a lower convex hull through (0, 0), e.g. KEEP -> COMPRESS ->
  DELETE. Each hull edge is an "upgrade" with an incremental cost per byte. All edges of
  all files are taken in increasing slope order until B >= R. A file can therefore be
  compressed first and upgraded to DELETE later if that is the next cheapest byte.
- **`heuristic` key:** (file, action) pairs in decreasing h order, with upgrades allowed.
  It isolates the effect of the heuristic weights.
- **Both:** skip choices that break a constraint, then drop redundant actions (costliest
  first) while the target stays met. O(m log m). Typical failure: the cheapest bytes may
  sit in a file far larger than needed, so the plan overshoots at a high absolute cost.

### Branch-and-bound (`branch_and_bound.py`)

Depth-first search over the state space with backtracking on mutable arrays (O(n) memory).

| Ingredient | Choice |
|---|---|
| Initial incumbent U | Best of the two greedy plans |
| Lower bound | g + LP(remaining units, remaining bytes): the LP relaxation of the multiple-choice knapsack (`relaxation.py`), i.e. hull edges of the remaining units filled in slope order, the last one fractionally. A *unit* is a file, or a whole duplicate group whose copies are all candidates. A group's hull is the exact convexification of "every copy but one may be deleted": the lower hull of the union, over the kept copy m, of the Minkowski sums of the members' hulls (m without DELETE). Admissible because it only drops integrality and the risk budget. |
| Pruning | Child pruned if g >= U, g > C_max, g + LP >= U, or it deletes a group's last copy |
| Goal cut-off | A node with B >= R is recorded and not expanded (keeping everything else is its cheapest completion) |
| File order | `ratio` (cheapest bytes first, default), `benefit`, or `problem`; members of a duplicate group are always kept next to each other so their unit can be bounded |
| Action order | `bound` (lowest g + LP first, default) or `heuristic` (highest h first) |
| Limits | Time / node limit, returning the best plan so far with gap = (cost - root LP) / cost |

If the search finishes, the plan is proven optimal; if the root LP is infeasible, the
target is proven unreachable.

### Exhaustive reference (`exhaustive.py`)

Plain DFS through `CleanupProblem.successors`, no bounding (n <= 16). It is used as
ground truth: the tests check that branch-and-bound matches it on 60 random instances
with every ordering option, and that the LP bound never exceeds the true optimum.

## Safety boundary

Sensing, analysis and planning are read-only. Only the executor (Phase 5) touches files,
and only for actions the user approved in the review step. DELETE always goes through the
Recycle Bin.
