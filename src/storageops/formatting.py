"""Human-readable formatting helpers shared by the CLI and GUI."""

from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(num_bytes: float) -> str:
    """Format a byte count using binary (1024) multiples, e.g. ``1536 -> '1.5 KB'``."""
    size = float(num_bytes)
    for unit in _UNITS:
        if abs(size) < 1024 or unit == _UNITS[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")
