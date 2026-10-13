"""Configuration loading for StorageOps.

Defaults live in the dataclasses below, so the package works without any
config file. ``config/default.toml`` documents every option; a copy of it can
be passed to :func:`load_config` to override values.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from storageops.models import FileCategory

DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        "$recycle.bin",
        "system volume information",
        "$windows.~bt",
        "$windows.~ws",
        "winsxs",
        ".git",
        ".svn",
        ".hg",
    }
)


def normalise_extension(ext: str) -> str:
    """Return ``ext`` lower-cased with exactly one leading dot (``"PDF"`` -> ``".pdf"``)."""
    ext = ext.strip().lower()
    return ext if ext.startswith(".") else f".{ext}"


@dataclass(frozen=True)
class ScannerConfig:
    """Options for the file-system scanner (Phase 1 sensor)."""

    include_hidden: bool = True
    follow_symlinks: bool = False
    max_depth: int | None = None
    min_file_size_bytes: int = 0
    excluded_dir_names: frozenset[str] = DEFAULT_EXCLUDED_DIRS
    excluded_extensions: frozenset[str] = frozenset()
    progress_interval: int = 500

    def __post_init__(self) -> None:
        if self.max_depth is not None and self.max_depth < 0:
            raise ValueError("scanner.max_depth must be >= 0")
        if self.min_file_size_bytes < 0:
            raise ValueError("scanner.min_file_size_bytes must be >= 0")
        if self.progress_interval < 1:
            raise ValueError("scanner.progress_interval must be >= 1")
        # Normalise so lookups can be case-insensitive and lists from TOML are accepted.
        object.__setattr__(
            self, "excluded_dir_names", frozenset(n.lower() for n in self.excluded_dir_names)
        )
        object.__setattr__(
            self,
            "excluded_extensions",
            frozenset(normalise_extension(e) for e in self.excluded_extensions),
        )


@dataclass(frozen=True)
class HashingConfig:
    """Options for staged duplicate detection."""

    algorithm: str = "blake2b"
    chunk_size_bytes: int = 1024 * 1024
    partial_bytes: int = 64 * 1024
    min_size_bytes: int = 1

    def __post_init__(self) -> None:
        if self.algorithm not in hashlib.algorithms_available:
            raise ValueError(f"hashing.algorithm {self.algorithm!r} is not available in hashlib")
        if self.chunk_size_bytes < 1:
            raise ValueError("hashing.chunk_size_bytes must be >= 1")
        if self.partial_bytes < 1:
            raise ValueError("hashing.partial_bytes must be >= 1")
        if self.min_size_bytes < 1:
            raise ValueError("hashing.min_size_bytes must be >= 1")


# --------------------------------------------------------------------------- Phase 2

_CATEGORIES = tuple(c.value for c in FileCategory)
# fmt: off
DEFAULT_CATEGORY_RISK = {
    "temporary": 0.05, "cache": 0.05, "log": 0.15, "installer": 0.2, "compressed": 0.4,
    "video": 0.5, "audio": 0.5, "other": 0.5, "image": 0.6, "executable": 0.6, "data": 0.7,
    "document": 0.8, "source_code": 0.8, "system": 1.0,
}
# fmt: on

# Expected archived size / original size (zip/LZMA-class compressor).
# fmt: off
DEFAULT_COMPRESSION_RATIO = {
    "temporary": 0.5, "cache": 0.6, "log": 0.15, "installer": 0.95, "compressed": 1.0,
    "video": 0.98, "audio": 0.97, "other": 0.7, "image": 0.95, "executable": 0.6,
    "data": 0.4, "document": 0.6, "source_code": 0.25, "system": 0.6,
}
# fmt: on

DEFAULT_SEVERITY = {"DELETE": 1.0, "ARCHIVE": 0.4, "MOVE": 0.25, "COMPRESS": 0.1}
DEFAULT_PENALTY = {"DELETE": 0.02, "ARCHIVE": 0.04, "MOVE": 0.03, "COMPRESS": 0.03}

DEFAULT_IMPORTANT_DIRS = frozenset(
    {"documents", "desktop", "pictures", "music", "videos", "onedrive"}
)
DEFAULT_LOW_VALUE_DIRS = frozenset({"downloads", "temp", "tmp", "cache", "logs"})

DEFAULT_PROTECTED_PATHS = ("%SystemRoot%", "%ProgramFiles%", "%ProgramFiles(x86)%", "%ProgramData%")
# fmt: off
DEFAULT_PROTECTED_PATTERNS = (
    "*.pem", "*.key", "*.pfx", "*.p12", "*.kdbx", "id_rsa*", "id_ed25519*", "wallet.dat",
    "desktop.ini", "ntuser.*", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
)
# fmt: on


def _merge_table(
    section: str, defaults: dict[str, float], given: dict[str, float], low: float, high: float
) -> dict[str, float]:
    """Overlay ``given`` on ``defaults``; reject unknown keys and out-of-range values."""
    unknown = set(given) - set(defaults)
    if unknown:
        raise ValueError(f"Unknown key(s) in [{section}]: {sorted(unknown)}")
    merged = {**defaults, **{k: float(v) for k, v in given.items()}}
    for key, value in merged.items():
        if not low <= value <= high:
            raise ValueError(f"[{section}] {key} = {value} is outside [{low}, {high}]")
    return merged


def _check_unit(section: str, **values: float) -> None:
    for name, value in values.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{section}.{name} must be within [0, 1]")


def _check_positive(section: str, **values: float) -> None:
    for name, value in values.items():
        if value <= 0:
            raise ValueError(f"{section}.{name} must be > 0")


@dataclass(frozen=True)
class RiskConfig:
    """Risk model: how much value would be lost if a file were removed (0 = none, 1 = critical)."""

    category_base: dict[str, float] = field(default_factory=dict)
    weight_category: float = 0.6
    weight_recency: float = 0.25
    weight_usage: float = 0.15
    recency_half_life_days: float = 90.0
    usage_half_life_days: float = 90.0
    hidden_penalty: float = 0.05
    readonly_penalty: float = 0.1
    important_dirs: frozenset[str] = DEFAULT_IMPORTANT_DIRS
    important_dir_boost: float = 0.2
    low_value_dirs: frozenset[str] = DEFAULT_LOW_VALUE_DIRS
    low_value_dir_relief: float = 0.15
    duplicate_factor: float = 0.25

    def __post_init__(self) -> None:
        merged = _merge_table(
            "risk.category_base", DEFAULT_CATEGORY_RISK, self.category_base, 0.0, 1.0
        )
        object.__setattr__(self, "category_base", merged)
        _check_unit(
            "risk",
            weight_category=self.weight_category,
            weight_recency=self.weight_recency,
            weight_usage=self.weight_usage,
        )
        if abs(self.weight_category + self.weight_recency + self.weight_usage - 1.0) > 1e-9:
            raise ValueError("risk weights (category + recency + usage) must sum to 1")
        _check_positive(
            "risk",
            recency_half_life_days=self.recency_half_life_days,
            usage_half_life_days=self.usage_half_life_days,
        )
        _check_unit(
            "risk",
            hidden_penalty=self.hidden_penalty,
            readonly_penalty=self.readonly_penalty,
            important_dir_boost=self.important_dir_boost,
            low_value_dir_relief=self.low_value_dir_relief,
            duplicate_factor=self.duplicate_factor,
        )
        object.__setattr__(
            self, "important_dirs", frozenset(d.casefold() for d in self.important_dirs)
        )
        object.__setattr__(
            self, "low_value_dirs", frozenset(d.casefold() for d in self.low_value_dirs)
        )


@dataclass(frozen=True)
class ActionConfig:
    """Benefit and cost model of each cleanup action."""

    severity: dict[str, float] = field(default_factory=dict)
    penalty: dict[str, float] = field(default_factory=dict)
    compression_ratio: dict[str, float] = field(default_factory=dict)
    inplace_compression_efficiency: float = 0.6
    min_compression_saving: float = 0.1
    min_benefit_bytes: int = 1

    def __post_init__(self) -> None:
        tables = {
            "severity": DEFAULT_SEVERITY,
            "penalty": DEFAULT_PENALTY,
            "compression_ratio": DEFAULT_COMPRESSION_RATIO,
        }
        for name, defaults in tables.items():
            merged = _merge_table(f"actions.{name}", defaults, getattr(self, name), 0.0, 1.0)
            object.__setattr__(self, name, merged)
        _check_unit(
            "actions",
            inplace_compression_efficiency=self.inplace_compression_efficiency,
            min_compression_saving=self.min_compression_saving,
        )
        if self.min_benefit_bytes < 1:
            raise ValueError("actions.min_benefit_bytes must be >= 1")


@dataclass(frozen=True)
class HeuristicConfig:
    """Weights of the search-guiding heuristic h(f, a). They need not sum to 1."""

    weight_benefit: float = 0.35
    weight_inactivity: float = 0.15
    weight_duplicate: float = 0.15
    weight_risk: float = 0.35
    inactivity_half_life_days: float = 180.0

    def __post_init__(self) -> None:
        for name in ("weight_benefit", "weight_inactivity", "weight_duplicate", "weight_risk"):
            if getattr(self, name) < 0:
                raise ValueError(f"heuristic.{name} must be >= 0")
        _check_positive("heuristic", inactivity_half_life_days=self.inactivity_half_life_days)


@dataclass(frozen=True)
class SafetyConfig:
    """Hard constraints. Protected files may only be KEPT; risky actions are forbidden."""

    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS
    protected_categories: frozenset[str] = frozenset({"system"})
    protect_system_files: bool = True
    protected_name_patterns: tuple[str, ...] = DEFAULT_PROTECTED_PATTERNS
    max_action_risk: float = 0.6

    def __post_init__(self) -> None:
        object.__setattr__(self, "protected_paths", tuple(self.protected_paths))
        object.__setattr__(self, "protected_name_patterns", tuple(self.protected_name_patterns))
        object.__setattr__(self, "protected_categories", frozenset(self.protected_categories))
        unknown = self.protected_categories - set(_CATEGORIES)
        if unknown:
            raise ValueError(f"safety.protected_categories has unknown values: {sorted(unknown)}")
        _check_unit("safety", max_action_risk=self.max_action_risk)


@dataclass(frozen=True)
class Config:
    """Top-level StorageOps configuration. New sections are added phase by phase."""

    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    hashing: HashingConfig = field(default_factory=HashingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    actions: ActionConfig = field(default_factory=ActionConfig)
    heuristic: HeuristicConfig = field(default_factory=HeuristicConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


_SECTIONS: dict[str, type] = {
    "scanner": ScannerConfig,
    "hashing": HashingConfig,
    "risk": RiskConfig,
    "actions": ActionConfig,
    "heuristic": HeuristicConfig,
    "safety": SafetyConfig,
}


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a TOML file, or return the defaults when ``path`` is None.

    Raises:
        ValueError: if the file contains unknown sections/keys or invalid values.
    """
    if path is None:
        return Config()
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ValueError(f"Unknown config section(s): {sorted(unknown)}")
    sections = {name: _build(cls, data.get(name, {}), name) for name, cls in _SECTIONS.items()}
    return Config(**sections)


def _build(cls: type, values: dict[str, Any], section: str) -> Any:
    allowed = {f.name for f in fields(cls)}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unknown key(s) in [{section}]: {sorted(unknown)}")
    return cls(**values)
