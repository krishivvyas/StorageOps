# StorageOps: Problem Formulation

## PEAS

| Element | Specification |
|---|---|
| **Performance** | Free-space target achieved; plan cost/risk; number of unnecessary actions; planning time; solution quality (gap to optimal). |
| **Environment** | A selected local storage location, or a synthetic benchmark instance: files with size, type, age, duplicate status and usage information. Partially observable (true importance is unknown), static during planning, discrete, single-agent. |
| **Actuators** | Per-file actions KEEP, DELETE, ARCHIVE, COMPRESS, MOVE, carried out only after explicit user approval. |
| **Sensors** | File-system scanner and metadata collector (`storageops.sensors`): path, size, type, timestamps, attributes, content hash, volume free space. |

## Action semantics

| Action | Effect | Space freed on source volume |
|---|---|---|
| KEEP | No change | 0 |
| DELETE | Send to Recycle Bin (recoverable) | size (until the bin is emptied) |
| ARCHIVE | Compress into an archive, verify the archive, remove the original | size x (1 - r_type) |
| COMPRESS | Transparent in-place compression; file stays usable at its path | size x (1 - r_type) |
| MOVE | Relocate without compression | size, only if the destination is on another volume |

r_type is the expected compression ratio for the file's category (for example, about 1.0
for already-compressed media, much lower for text and logs).

## Formal model

Let F = {f_1, ..., f_n} be the eligible files and T the required free space.
Each file f_i gets exactly one action a_i from A(f_i), its allowed actions (protected files
only allow KEEP). Each choice has

- benefit b(f_i, a_i): bytes freed on the target volume
- cost c(f_i, a_i): estimated risk + unnecessary-action penalty + operation cost

| Component | Definition |
|---|---|
| State | (k, assignment of a_1..a_k, freed so far B_k = sum b, cost so far C_k = sum c) |
| Initial state | k = 0, nothing assigned, current free space F_0, target T |
| Successors | Assign an allowed action to the next undecided file f_(k+1) |
| Goal test | F_0 + B_n >= T, all safety constraints hold, and C_n <= risk budget |
| Path cost | C_n = sum of c(f_i, a_i) over all files |

Objective: minimise sum c(f_i, a_i) subject to F_0 + sum b(f_i, a_i) >= T and safety
constraints. This is a minimisation form of the **multiple-choice knapsack problem**, which
is NP-hard. That motivates comparing:

- **Greedy baseline:** rank (file, action) pairs by benefit/cost and take them until the target is met. Fast, but not optimal.
- **Branch-and-bound:** depth-first search over assignments, pruned with an admissible lower bound from the fractional (LP) relaxation of the remaining files. Optimal, with exponential worst case.

## Safety boundary

Sensing and planning are read-only. Only the executor (Phase 5) touches files, and only
for actions the user approved in the review step. DELETE always goes through the Recycle Bin.
