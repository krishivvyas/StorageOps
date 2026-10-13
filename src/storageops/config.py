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


@dataclass(frozen=True)
class Config:
    """Top-level StorageOps configuration. New sections are added phase by phase."""

    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    hashing: HashingConfig = field(default_factory=HashingConfig)


_SECTIONS: dict[str, type] = {"scanner": ScannerConfig, "hashing": HashingConfig}


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
