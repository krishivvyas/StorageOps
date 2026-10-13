"""Append-only audit log of every executed (or dry-run) operation, as JSON Lines."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from storageops.paths import journal_dir


class Journal:
    """One file per execution session: ``<dir>/YYYYmmdd-HHMMSS.jsonl``.

    Each line records what was done to which file, and where the data went
    (Recycle Bin, archive path or move destination), so a run can be audited or
    undone by hand.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = journal_dir() / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        self.path = Path(path)

    def record(self, **entry: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"time": time.time(), **entry}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
