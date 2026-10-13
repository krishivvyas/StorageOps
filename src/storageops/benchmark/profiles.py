"""Benchmark instance specifications: what to generate, and the standard suites."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from storageops.models import FileCategory as C

SIZE_CLASSES: dict[str, int] = {"small": 10, "medium": 150, "large": 1500}
"""Files per instance. "small" stays within reach of exhaustive search."""

# Category mix per risk level: probability of each category among generated files.
CATEGORY_MIX: dict[str, dict[C, float]] = {
    "low": {
        C.TEMPORARY: 0.22, C.CACHE: 0.18, C.LOG: 0.10, C.INSTALLER: 0.15, C.COMPRESSED: 0.10,
        C.VIDEO: 0.07, C.IMAGE: 0.05, C.OTHER: 0.08, C.DOCUMENT: 0.03, C.SYSTEM: 0.02,
    },
    "mixed": {
        C.TEMPORARY: 0.08, C.CACHE: 0.08, C.LOG: 0.07, C.INSTALLER: 0.07, C.COMPRESSED: 0.07,
        C.VIDEO: 0.08, C.AUDIO: 0.06, C.IMAGE: 0.12, C.DOCUMENT: 0.12, C.SOURCE_CODE: 0.08,
        C.DATA: 0.07, C.EXECUTABLE: 0.04, C.OTHER: 0.04, C.SYSTEM: 0.02,
    },
    "high": {
        C.DOCUMENT: 0.28, C.SOURCE_CODE: 0.18, C.IMAGE: 0.15, C.DATA: 0.14, C.VIDEO: 0.06,
        C.AUDIO: 0.04, C.OTHER: 0.06, C.TEMPORARY: 0.04, C.CACHE: 0.03, C.SYSTEM: 0.02,
    },
}  # fmt: skip

# Typical file age (days since modification, lognormal median) per risk level.
MEDIAN_AGE_DAYS: dict[str, float] = {"low": 400.0, "mixed": 150.0, "high": 30.0}


@dataclass(frozen=True)
class InstanceSpec:
    """Parameters of one synthetic storage environment. Same spec + seed = same instance."""

    size_class: str
    n_files: int
    duplicate_ratio: float = 0.2  # fraction of files that are redundant copies
    risk_level: str = "mixed"  # "low" | "mixed" | "high"
    target_fraction: float = 0.2  # bytes to free, as a fraction of all unprotected bytes
    seed: int = 0
    move_enabled: bool = False  # offer MOVE to a second volume (makes most instances easy)

    def __post_init__(self) -> None:
        if self.risk_level not in CATEGORY_MIX:
            raise ValueError(f"unknown risk level {self.risk_level!r}")
        if self.n_files < 1:
            raise ValueError("n_files must be >= 1")
        if not 0 <= self.duplicate_ratio < 1:
            raise ValueError("duplicate_ratio must be in [0, 1)")
        if not 0 < self.target_fraction <= 1:
            raise ValueError("target_fraction must be in (0, 1]")

    @property
    def name(self) -> str:
        return (
            f"{self.size_class}-n{self.n_files}-dup{self.duplicate_ratio:g}-"
            f"{self.risk_level}-t{self.target_fraction:g}-s{self.seed}"
        )


def benchmark_suite(
    size_classes: Iterable[str] = SIZE_CLASSES,
    *,
    seeds: Iterable[int] = range(3),
    duplicate_ratios: Sequence[float] = (0.0, 0.3),
    risk_levels: Sequence[str] = ("low", "mixed", "high"),
    target_fractions: Sequence[float] = (0.1, 0.3),
) -> list[InstanceSpec]:
    """Full factorial grid: every size class x duplicate ratio x risk x target x seed."""
    seeds = list(seeds)
    return [
        InstanceSpec(size, SIZE_CLASSES[size], dup, risk, target, seed)
        for size, dup, risk, target, seed in itertools.product(
            size_classes, duplicate_ratios, risk_levels, target_fractions, seeds
        )
    ]


def scaling_suite(
    sizes: Sequence[int] = (10, 20, 40, 80, 160, 320, 640, 1280),
    *,
    seeds: Iterable[int] = range(3),
    risk_level: str = "mixed",
    duplicate_ratio: float = 0.2,
    target_fraction: float = 0.2,
) -> list[InstanceSpec]:
    """Same profile at growing file counts, to measure how cost and effort scale with n."""
    seeds = list(seeds)
    return [
        InstanceSpec(f"n{n}", n, duplicate_ratio, risk_level, target_fraction, seed)
        for n in sizes
        for seed in seeds
    ]
