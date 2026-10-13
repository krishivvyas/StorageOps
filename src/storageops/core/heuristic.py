"""Heuristic h(f, a): how desirable an action looks. It guides the search; it is not the objective.

For an allowed removal action a:

    h(f, a) = w_b * B(f, a) + w_i * I(f) + w_d * D(f) - w_r * severity(a) * R(f)

    B(f, a) = log(1 + benefit) / log(1 + largest benefit among allowed pairs)   in [0, 1]
    I(f)    = 1 - 0.5 ** (age_days / inactivity_half_life)                      in [0, 1)
    D(f)    = 1 if f belongs to a duplicate group, else 0
    R(f)    = risk from core.risk

h(f, KEEP) = 0 and disallowed pairs get -inf. The log scale stops a few huge
files from flattening every other benefit to ~0.

Planners order their choices by h. Optimality is always judged by the cost
function in core.actions, so the weights can be tuned experimentally
(Phase 4) without changing which plan is optimal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from storageops.config import ActionConfig, HeuristicConfig
from storageops.core.actions import REMOVAL_ACTIONS, ActionTables
from storageops.models import Action


def heuristic_scores(
    files: pd.DataFrame,
    risk: pd.Series,
    tables: ActionTables,
    config: HeuristicConfig | None = None,
    action_config: ActionConfig | None = None,
) -> pd.DataFrame:
    config = config or HeuristicConfig()
    action_config = action_config or ActionConfig()

    usable = tables.benefit.where(tables.allowed, 0).drop(columns=Action.KEEP.value)
    largest = int(usable.to_numpy().max()) if usable.size else 0
    inactivity = 1.0 - 0.5 ** (files["age_days"].astype(float) / config.inactivity_half_life_days)
    duplicate = files["duplicate_group"].notna().astype(float)
    shared = config.weight_inactivity * inactivity + config.weight_duplicate * duplicate

    scores = pd.DataFrame(0.0, index=files.index, columns=tables.benefit.columns)
    for action in REMOVAL_ACTIONS:
        benefit = tables.benefit[action.value].astype(float)
        normalised = np.log1p(benefit) / np.log1p(largest) if largest > 0 else benefit * 0.0
        scores[action.value] = (
            config.weight_benefit * normalised
            + shared
            - config.weight_risk * action_config.severity[action.value] * risk
        )
    return scores.where(tables.allowed, -np.inf)
