"""Convert scan results to pandas DataFrames, summarise them and persist them."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
from pathlib import Path

import pandas as pd

from storageops.models import FileCategory, FileRecord

COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(FileRecord))

_DTYPES: dict[str, str] = {
    "path": "string",
    "name": "string",
    "extension": "string",
    "size_bytes": "int64",
    "created_ts": "float64",
    "modified_ts": "float64",
    "accessed_ts": "float64",
    "age_days": "float64",
    "idle_days": "float64",
    "depth": "int64",
    "is_hidden": "bool",
    "is_system": "bool",
    "is_readonly": "bool",
    "content_hash": "string",
    "duplicate_group": "Int64",  # nullable: most files are not duplicates
    "is_duplicate": "bool",
}
_CATEGORIES = [c.value for c in FileCategory]


def records_to_frame(records: Iterable[FileRecord]) -> pd.DataFrame:
    """Build the canonical file DataFrame (one row per file, columns = FileRecord fields)."""
    frame = pd.DataFrame([r.to_dict() for r in records], columns=list(COLUMNS))
    return _coerce(frame)


def _coerce(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Dataset is missing column(s): {sorted(missing)}")
    frame = frame.loc[:, list(COLUMNS)].astype(_DTYPES)
    frame["category"] = pd.Categorical(frame["category"].astype(str), categories=_CATEGORIES)
    return frame


def save_dataset(frame: pd.DataFrame, path: str | Path) -> Path:
    """Write ``frame`` as CSV or Parquet, chosen by the file extension."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame.to_csv(path, index=False)
    elif suffix == ".parquet":
        frame.to_parquet(path, index=False)  # requires the optional pyarrow dependency
    else:
        raise ValueError(f"Unsupported dataset format {suffix!r}; use .csv or .parquet")
    return path


def load_dataset(path: str | Path) -> pd.DataFrame:
    """Read a dataset written by :func:`save_dataset`, restoring column dtypes."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        # Only the nullable columns may be NA. A file literally named "NA" or
        # with no extension must survive the round trip.
        frame = pd.read_csv(
            path,
            dtype={"path": str, "name": str, "extension": str, "content_hash": str},
            keep_default_na=False,
            na_values={"content_hash": [""], "duplicate_group": [""]},
        )
    elif suffix == ".parquet":
        frame = pd.read_parquet(path)
    else:
        raise ValueError(f"Unsupported dataset format {suffix!r}; use .csv or .parquet")
    return _coerce(frame)


def category_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-category file count, total size, share of scanned bytes and median age."""
    total = int(frame["size_bytes"].sum())
    summary = (
        frame.groupby("category", observed=True)
        .agg(
            files=("path", "size"),
            total_bytes=("size_bytes", "sum"),
            share=("size_bytes", lambda s: s.sum() / total if total else 0.0),
            median_age_days=("age_days", "median"),
            duplicate_files=("is_duplicate", "sum"),
        )
        .sort_values("total_bytes", ascending=False)
        .reset_index()
    )
    summary["category"] = summary["category"].astype(str)
    return summary


def duplicate_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per duplicate group, with its retained original and reclaimable bytes."""
    members = frame[frame["duplicate_group"].notna()]
    columns = ["duplicate_group", "copies", "size_bytes", "reclaimable_bytes", "original_path"]
    if members.empty:
        return pd.DataFrame(columns=columns)
    grouped = members.groupby("duplicate_group").agg(
        copies=("path", "size"), size_bytes=("size_bytes", "first")
    )
    grouped["reclaimable_bytes"] = grouped["size_bytes"] * (grouped["copies"] - 1)
    originals = members[~members["is_duplicate"]].set_index("duplicate_group")["path"]
    grouped["original_path"] = originals
    return (
        grouped.reset_index()
        .sort_values("reclaimable_bytes", ascending=False, kind="stable")
        .loc[:, columns]
        .reset_index(drop=True)
    )


def largest_files(frame: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    return frame.nlargest(n, "size_bytes")[["path", "category", "size_bytes", "age_days"]]
