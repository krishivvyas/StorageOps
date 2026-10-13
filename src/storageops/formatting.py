"""Human-readable formatting helpers shared by the CLI and GUI."""

from __future__ import annotations

import re

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(num_bytes: float) -> str:
    """Format a byte count using binary (1024) multiples, e.g. ``1536 -> '1.5 KB'``."""
    size = float(num_bytes)
    for unit in _UNITS:
        if abs(size) < 1024 or unit == _UNITS[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp](?:i?b)?|b)?\s*$", re.IGNORECASE)
_MULTIPLIERS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4, "p": 1024**5}


def parse_size(text: str) -> int:
    """Parse a size such as ``"500"``, ``"20GB"``, ``"1.5 g"`` or ``"750MiB"`` into bytes.

    Units are binary (1 KB = 1024 B), matching :func:`format_bytes`.
    """
    match = _SIZE_PATTERN.match(text)
    if not match:
        raise ValueError(f"invalid size: {text!r} (examples: 500MB, 20GB, 1.5TB)")
    number, unit = match.groups()
    prefix = (unit or "").lower().rstrip("b").rstrip("i")
    return int(float(number) * _MULTIPLIERS[prefix])
