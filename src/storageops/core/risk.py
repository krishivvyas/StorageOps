"""Risk model: estimated loss of value if a file were removed.

For file f:

    base(f)   = w_c * category_base(f) + w_r * recency(f) + w_u * usage(f)
    raw(f)    = clip(base(f) + location(f) + flags(f), 0, 1)
    R(f)      = raw(f) * (duplicate_factor if f is in a duplicate group else 1)

    recency(f)  = 0.5 ** (age_days / recency_half_life)   1 = modified just now
    usage(f)    = 0.5 ** (idle_days / usage_half_life)    1 = accessed just now
    location(f) = +boost  if any parent folder is an "important" folder (Documents, ...)
                  -relief if any parent folder is a "low-value" folder (Downloads, Temp, ...)
    flags(f)    = hidden_penalty * hidden + readonly_penalty * read-only

The duplicate discount is valid because the state space forbids deleting
every copy of a duplicate group (see core.state), so the content always
survives somewhere.
"""

from __future__ import annotations

from pathlib import PurePath

import numpy as np
import pandas as pd

from storageops.config import RiskConfig

RISK_COLUMNS = (
    "risk_category",
    "risk_recency",
    "risk_usage",
    "risk_location",
    "risk_flags",
    "risk_raw",
    "duplicate_factor",
    "risk",
)


def assess_risk(files: pd.DataFrame, config: RiskConfig | None = None) -> pd.DataFrame:
    """Return the risk breakdown (``RISK_COLUMNS``) for every row of ``files``.

    Every component is kept, not only the final ``risk``, so the GUI and report
    can explain each estimate.
    """
    config = config or RiskConfig()
    category = files["category"].astype(str).map(config.category_base).astype(float)
    recency = 0.5 ** (files["age_days"].astype(float) / config.recency_half_life_days)
    usage = 0.5 ** (files["idle_days"].astype(float) / config.usage_half_life_days)

    folders = files["path"].map(_parent_folders)
    important = folders.map(lambda parts: not parts.isdisjoint(config.important_dirs))
    low_value = folders.map(lambda parts: not parts.isdisjoint(config.low_value_dirs))
    location = (
        important.astype(float) * config.important_dir_boost
        - low_value.astype(float) * config.low_value_dir_relief
    )
    flags = (
        files["is_hidden"].astype(float) * config.hidden_penalty
        + files["is_readonly"].astype(float) * config.readonly_penalty
    )

    base = (
        config.weight_category * category
        + config.weight_recency * recency
        + config.weight_usage * usage
    )
    raw = (base + location + flags).clip(0.0, 1.0)
    duplicate_factor = pd.Series(
        np.where(files["duplicate_group"].notna(), config.duplicate_factor, 1.0),
        index=files.index,
    )

    return pd.DataFrame(
        {
            "risk_category": category,
            "risk_recency": recency,
            "risk_usage": usage,
            "risk_location": location,
            "risk_flags": flags,
            "risk_raw": raw,
            "duplicate_factor": duplicate_factor,
            "risk": raw * duplicate_factor,
        },
        index=files.index,
        columns=list(RISK_COLUMNS),
    ).astype(float)


def _parent_folders(path: str) -> frozenset[str]:
    return frozenset(part.casefold() for part in PurePath(path).parent.parts)
