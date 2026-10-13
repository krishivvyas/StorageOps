# StorageOps: Experimental Evaluation and Failure Analysis

All numbers come from `storageops bench` (full settings: 3 seeds, 5 s branch-and-bound time
limit), run on Windows 11 / Python 3.12. Regenerate with `python scripts/run_benchmarks.py`.
The tables behind every statement are in `results/summary.md`; raw rows are in
`results/*.csv`. Instance generation is deterministic, so quality numbers reproduce exactly;
timings depend on the machine.

## 1. Experimental setup

**Instances.** These are synthetic storage environments (`benchmark/generator.py`) with the
same columns as a real scan, so they go through the identical analysis and planning pipeline:

- categories drawn from a risk-level mix:
  - *low*: mostly temp, cache, logs and installers, about 400 days old;
  - *mixed*;
  - *high*: mostly documents, source code and data, about 30 days old;
- lognormal sizes around per-category medians (video ~700 MB, source files ~20 KB);
- typical folders (Temp, Downloads, Documents, ...);
- a share of redundant duplicate copies;
- 2% protected system files.

The free-space requirement is a fraction (10% or 30%) of all unprotected bytes. MOVE is
disabled by default; section 5 explains why.

**Design.**
- *Main grid:* 3 size classes (10 / 150 / 1,500 files) × duplicate ratio {0, 0.3} × risk
  level {low, mixed, high} × target {0.1, 0.3} × 3 seeds = **108 instances**.
- *Scaling study:* 10 to 2,560 files, mixed risk, 3 seeds each.
- *Weight study:* 6 medium instances; each heuristic weight swept from 0 to 1 while the others
  stay at their defaults.

**Planners.**
- *Greedy (ratio):* the default baseline.
- *Greedy (heuristic):* ranks by the heuristic h.
- *Branch-and-bound:* bound-ordered actions.
- *Branch-and-bound (h-order):* heuristic-ordered actions.
- *Exhaustive:* small instances only.

**Quality** = excess cost over the cheapest goal plan any planner found on the instance.
Branch-and-bound proved optimality on every instance except one run (section 3.4), so this
is excess over the **true optimum**.

## 2. Headline results

| | Greedy (ratio) | Greedy (heuristic) | Branch & bound | Exhaustive |
|---|---|---|---|---|
| Plans equal to the optimum | 57% | 35% | 100% | 100% (small only) |
| Mean excess cost: small / medium / large | 11% / 7.9% / 2.0% | 43% / 45% / 257% | 0 / 0 / 0 | 0 |
| Worst excess cost | 220% | 1,157% | 0 | 0 |
| Median time at 2,560 files | 15 ms | 2.3 ms | 114 ms | infeasible beyond ~12 files |
| Median nodes at 10 files | n/a | n/a | 4 | 50,184 |

- **Branch-and-bound is the method of choice.** It was optimal everywhere and finished
  within 3.8 s on the hardest 1,500-file instance. Its median time grows roughly linearly
  with instance size: 0.9 ms at 10 files, 114 ms at 2,560.
- **Greedy (ratio) is a strong baseline on large instances (2% mean excess) but unreliable
  on small ones.** Section 3.1 explains why.
- **The bound does almost all of the work.** On 10-file instances branch-and-bound
  generates 4 nodes against exhaustive search's 50,184: a ~12,500× reduction from the LP
  bound plus the greedy incumbent.

## 3. Failure analysis

### 3.1 Greedy overshoot: the cheapest bytes are not the cheapest plan

Greedy's worst cases are all single-action plans that massively overshoot the target. On
`small-n10-dup0.3-mixed-t0.1-s1`, greedy deleted one file and freed **6.4× the required
bytes** at cost 0.42, while the optimum cost 0.13 (+220%). Greedy ranks by cost *per
byte*: a big file with excellent cost-per-byte wins even when the target needs only a
fraction of it. The flat per-action cost and the file's risk are then paid in full.

This hurts small instances most, because the few available files rarely fit the target well
(11% mean excess at 10 files vs 2% at 1,500). It is the classic weakness of knapsack
greedy, and the main reason branch-and-bound exists. A cheap mitigation is to also try
"the single cheapest file that alone meets the target" and keep the better plan. It is not
implemented, because branch-and-bound already covers this case.

### 3.2 The heuristic is a poor objective proxy

Greedy (heuristic) is 2 to 3 times worse than greedy (ratio) on small/medium instances, and
on large ones its mean excess reaches 257% (proposing 183 actions on average, vs 33 for the
optimum). Its score h(f, a) rates the *desirability of touching a file*, not the cost per
byte freed. It therefore happily picks many small, old, low-risk files, each paying the
per-action penalty.

The weight study (`figures/sensitivity_quality.png`) confirms this:

- **Benefit weight up = better.** Excess drops from 132% at w_b = 0 to 61% at w_b = 1:
  the closer h gets to "bytes freed", the better greedy does.
- **Any positive duplicate weight hurts** (34% at 0, 95% at the 0.15 default, 143% from
  0.25 up). It rewards touching small duplicate files that free little.
- **Risk weight 0 is best (41%).** Risk is already inside the cost function, so adding it
  to h again double-counts it and distorts the ranking.
- **Inactivity has a narrow sweet spot** (minimum 80% at 0.25).

**Conclusion:** no setting of the four weights makes heuristic greedy competitive with
ratio greedy (its best is 34%, vs 8% for ratio greedy on medium instances in the main grid). This is why the default greedy ranks by cost per byte,
and why h is used only for ordering, never for judging plans.

### 3.3 The heuristic barely affects branch-and-bound

The h-ordered branch-and-bound generated 127 nodes on average **for every weight setting**
(`figures/sensitivity_nodes.png`). With an incumbent from greedy and a tight LP bound, almost
every branch is pruned before action ordering matters. Across the main grid, both action
orders found the same plans. The bound order was more robust on the hardest instance
(section 3.4), which is why it is the default.

### 3.4 Time limits

Only **1 of 270** branch-and-bound runs (main grid and scaling study, both action orders) hit the 5 s limit: h-order on
`large-n1500-dup0.3-low-t0.3-s2`. It returned a plan 0.31% above optimal with a certified
gap of 0.94%. The bound-ordered variant solved the same instance to optimality in 3.8 s. The
hard cases are large, duplicate-rich, low-risk instances with a high target. There, many
near-identical cheap options (temp files, copies) create many plans of almost equal cost,
which the bound cannot separate quickly.

### 3.5 Infeasible targets: the safety rules can make a target unreachable

25 of 108 instances (23%) are **proven infeasible**. Branch-and-bound reports this, and
greedy reports "no plan found" on the same instances:

| Risk level | Feasible | Proven infeasible |
|---|---|---|
| low | 100% | 0% |
| mixed | 92% | 8% |
| high | 39% | 61% |

This is the safety boundary working as designed, not a planner failure. In high-risk
environments most bytes belong to recent documents and source code. For those files DELETE
breaks the risk threshold (severity × risk > 0.6), and ARCHIVE/COMPRESS free only part of
each file. In 23 of the 25 cases even the most generous allowed action per file cannot reach
the target. In the other 2, the remaining shortfall comes from the "keep one copy" rule.

Two observations:
- **Duplicates make targets easier** (18 infeasible instances without duplicates vs 7 with):
  redundant copies have discounted risk, so deleting them is allowed.
- **Infeasibility is detected in milliseconds**, usually at the root, because the LP bound
  is already infinite.

For the user this means: StorageOps tells you the target cannot be reached *safely*, instead
of quietly deleting important files. The prototype should also report the largest
achievable safe target (`max_freeable_bytes`, already shown by `analyze`).

### 3.6 A weakness found and fixed by benchmarking: duplicate groups and the LP bound

The first benchmark runs showed branch-and-bound timing out on large instances with large
reported gaps. Without duplicates, the same instances solved optimally in a few hundred nodes:

| Instance (1,500 files, mixed risk, target 30%) | Before fix | After fix |
|---|---|---|
| no duplicates | optimal, 444 nodes | optimal, 444 nodes |
| 10% duplicates | 44.5% gap, 127,820 nodes (time-out) | **optimal, 3,760 nodes, 0.1 s** |
| 30% duplicates | 65.7% gap, 139,236 nodes (time-out) | **optimal, 23,092 nodes, 0.8 s** |

**Cause.** The LP relaxation ignored the "never delete every copy" constraint. Duplicates
carry a ×0.25 risk discount, so the relaxation "deleted" all copies of every group at almost
no cost. The bound collapsed and almost nothing was pruned.

**Fix** (`planners/relaxation.py`). A duplicate group whose copies are all candidates is
bounded as one unit, using the exact convex hull of "every copy but one may be deleted". The
search order keeps group members next to each other so that unit can be used. The bound stays
admissible: tests check that branch-and-bound still matches exhaustive search on random
instances with groups of two and three, and with scattered members.

### 3.7 MOVE trivialises the problem

With MOVE enabled (a second drive available), almost every instance is solved by moving one
or two large videos. MOVE frees the whole file at a quarter of DELETE's risk, so no real
trade-off remains: in a 12-configuration probe at 1,500 files, every instance was proven optimal
in under 5,000 nodes.
MOVE is therefore disabled in the default benchmark, and `InstanceSpec(move_enabled=True)`
re-enables it. In practice a user with a spare drive gets near-zero-risk plans. That is a
legitimate result, but it says little about the planners.

## 4. Threats to validity

- **Synthetic data.** Category mixes, size and age distributions are plausible but not
  fitted to real disks. The risk model is hand-weighted (Phase 2), so "optimal" means
  optimal *under this cost model*, not "what a user would choose".
- **Reference optimum.** Quality is relative to the best plan any planner found. This equals
  the proven optimum on every instance in the grid; the single time-limited run has its gap
  reported separately.
- **Timing** is single-threaded Python on one machine. Relative comparisons are meaningful;
  absolute milliseconds are not portable.
- **Exhaustive search** is only feasible up to ~12 files, so ground truth on medium and
  large instances rests on branch-and-bound's optimality proof. Branch-and-bound itself is
  checked against exhaustive search on 120 random small instances (all ordering variants) in
  the test suite.
- **A methodology bug was caught by the tests.** The first generator derived seeds from only
  the first 8 bytes of the instance name, so all seeds of a size class were identical. Seeds
  are now hashed from the full name, a regression test guards this, and every number above
  comes from runs after the fix.

## 5. Conclusions

1. Branch-and-bound with a group-aware LP bound solves realistic instances of up to 2,560
   candidate files to proven optimality, typically in well under a second.
2. Ratio greedy is a good fast fallback for large instances (about 2% from optimal), but it
   can be several times worse on small ones because of overshoot.
3. The heuristic is useful for explaining and ordering choices, but it is a poor objective.
   Its weights affect greedy quality strongly and branch-and-bound effort hardly at all.
4. Infeasible targets come mostly from the safety rules in high-risk environments. This is
   the intended behaviour, and it is detected instantly.
