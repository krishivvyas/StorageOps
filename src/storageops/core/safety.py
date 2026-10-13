"""Hard safety constraints: which files must never be touched.

A protected file is excluded from the search entirely; its only allowed action
is KEEP. Each protected file gets a reason, in priority order:

1. it lies under a protected location (Windows, Program Files, ProgramData, ...)
2. it has the Windows "system" attribute
3. its category is protected (system files by default)
4. its name matches a protected pattern (keys, certificates, password vaults, ...)
"""

from __future__ import annotations

import fnmatch
import os
import re

import pandas as pd

from storageops.config import SafetyConfig


def protected_prefixes(config: SafetyConfig) -> tuple[str, ...]:
    """Expand environment variables in the protected paths and normalise them.

    Entries whose variables are undefined on this machine are dropped. Each
    prefix ends with a separator, so ``C:\\Windows`` does not also protect
    ``C:\\Windows2``.
    """
    prefixes = []
    for raw in config.protected_paths:
        expanded = os.path.expandvars(raw)
        if "%" in expanded or "$" in expanded:  # variable not defined here
            continue
        normalised = os.path.normcase(os.path.normpath(expanded))
        prefixes.append(normalised.rstrip(os.sep) + os.sep)
    return tuple(prefixes)


def assess_protection(files: pd.DataFrame, config: SafetyConfig | None = None) -> pd.DataFrame:
    """Return ``protected`` (bool) and ``protection_reason`` (str, "" if unprotected)."""
    config = config or SafetyConfig()
    reason = pd.Series("", index=files.index, dtype="object")

    def mark(mask: pd.Series, text: str) -> None:
        reason[(reason == "") & mask.astype(bool)] = text

    prefixes = protected_prefixes(config)
    if prefixes:
        paths = files["path"].map(lambda p: os.path.normcase(os.path.normpath(p)))
        mark(paths.map(lambda p: p.startswith(prefixes)), "protected location")
    if config.protect_system_files:
        mark(files["is_system"], "system file attribute")
    if config.protected_categories:
        mark(files["category"].astype(str).isin(config.protected_categories), "protected category")
    if config.protected_name_patterns:
        pattern = re.compile(
            "|".join(fnmatch.translate(p) for p in config.protected_name_patterns), re.IGNORECASE
        )
        mark(files["name"].map(lambda n: pattern.match(n) is not None), "protected name pattern")

    return pd.DataFrame(
        {"protected": reason != "", "protection_reason": reason.astype("string")},
        index=files.index,
    )
