"""Synthetic storage environments with realistic file metadata. Nothing is written to disk.

Each instance is a Phase 1-compatible file table (the same columns as a real
scan), so it goes through exactly the same analysis and planning pipeline:

* categories drawn from the risk level's mix (``profiles.CATEGORY_MIX``);
* sizes lognormal around a per-category median (videos ~700 MB, source files ~20 KB);
* folders typical for the category (Temp, Downloads, Documents, ...), which feed
  the risk model's location term;
* ages lognormal around the risk level's median, with access no older than modification;
* a share of the files are redundant copies of others (same size and content
  hash), labelled with the same "oldest copy is the original" rule as the
  real duplicate detector.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from storageops.benchmark.profiles import CATEGORY_MIX, MEDIAN_AGE_DAYS, InstanceSpec
from storageops.config import Config
from storageops.core.analysis import StorageAnalysis, analyze
from storageops.core.state import CleanupProblem
from storageops.models import FileCategory as C
from storageops.models import FileRecord
from storageops.sensors.dataset import records_to_frame

BENCH_NOW = 1_760_000_000.0  # fixed clock: instances do not depend on today's date
DAY = 86_400.0
ROOT = "C:\\Bench\\Users\\me"
MOVE_DESTINATION = "E:\\Archive"  # another volume, so MOVE frees space on C:

MiB = 1024**2
_MEDIAN_SIZE: dict[C, float] = {
    C.TEMPORARY: 0.2 * MiB, C.CACHE: 0.05 * MiB, C.LOG: 1 * MiB, C.INSTALLER: 150 * MiB,
    C.COMPRESSED: 50 * MiB, C.VIDEO: 700 * MiB, C.AUDIO: 6 * MiB, C.IMAGE: 3 * MiB,
    C.DOCUMENT: 0.5 * MiB, C.SOURCE_CODE: 0.02 * MiB, C.DATA: 5 * MiB,
    C.EXECUTABLE: 10 * MiB, C.OTHER: 1 * MiB, C.SYSTEM: 2 * MiB,
}  # fmt: skip
_FOLDER: dict[C, str] = {
    C.TEMPORARY: "AppData\\Local\\Temp", C.CACHE: "AppData\\Local\\Browser\\Cache",
    C.LOG: "AppData\\Local\\App\\Logs", C.INSTALLER: "Downloads", C.COMPRESSED: "Downloads",
    C.VIDEO: "Videos", C.AUDIO: "Music", C.IMAGE: "Pictures", C.DOCUMENT: "Documents",
    C.SOURCE_CODE: "Projects\\app\\src", C.DATA: "Projects\\app\\data", C.EXECUTABLE: "Tools",
    C.OTHER: "Misc", C.SYSTEM: "AppData\\Local\\Drivers",
}  # fmt: skip
_EXTENSION: dict[C, str] = {
    C.TEMPORARY: ".tmp", C.CACHE: ".cache", C.LOG: ".log", C.INSTALLER: ".msi",
    C.COMPRESSED: ".zip", C.VIDEO: ".mp4", C.AUDIO: ".mp3", C.IMAGE: ".jpg",
    C.DOCUMENT: ".docx", C.SOURCE_CODE: ".py", C.DATA: ".csv", C.EXECUTABLE: ".exe",
    C.OTHER: ".dat", C.SYSTEM: ".sys",
}  # fmt: skip
_SIZE_SIGMA = 1.0
_AGE_SIGMA = 1.0


@dataclass(frozen=True)
class BenchmarkInstance:
    spec: InstanceSpec
    files: pd.DataFrame
    free_bytes: int
    target_free_bytes: int
    move_destination: str | None

    def analyze(self, config: Config | None = None) -> StorageAnalysis:
        return analyze(self.files, config, move_destination=self.move_destination)

    def problem(
        self, config: Config | None = None, analysis: StorageAnalysis | None = None
    ) -> CleanupProblem:
        analysis = analysis or self.analyze(config)
        return CleanupProblem.from_analysis(
            analysis, free_bytes=self.free_bytes, target_free_bytes=self.target_free_bytes
        )


def generate_instance(spec: InstanceSpec) -> BenchmarkInstance:
    rng = np.random.default_rng(_seed(spec))
    n_copies = int(round(spec.duplicate_ratio * spec.n_files))
    n_unique = max(1, spec.n_files - n_copies)
    n_copies = spec.n_files - n_unique

    mix = CATEGORY_MIX[spec.risk_level]
    categories = list(mix)
    probabilities = np.array([mix[c] for c in categories])
    probabilities /= probabilities.sum()
    drawn = rng.choice(len(categories), size=n_unique, p=probabilities)

    records = [
        _make_file(rng, categories[k], index, spec.risk_level) for index, k in enumerate(drawn)
    ]
    records.extend(_make_copies(rng, records, n_copies, spec.risk_level))
    records = _label_duplicates(records)

    files = records_to_frame(records)
    unprotected = files.loc[files["category"] != C.SYSTEM.value, "size_bytes"].sum()
    required = int(math.ceil(spec.target_fraction * unprotected))
    free = 0  # absolute free space is irrelevant to planning; only the requirement matters
    return BenchmarkInstance(
        spec=spec,
        files=files,
        free_bytes=free,
        target_free_bytes=free + required,
        move_destination=MOVE_DESTINATION if spec.move_enabled else None,
    )


def _seed(spec: InstanceSpec) -> int:
    # Stable across runs and processes (unlike hash()), and every character of the name
    # matters: specs differing only in seed or ratio must not share a random stream.
    return int.from_bytes(hashlib.blake2b(spec.name.encode(), digest_size=8).digest(), "little")


def _make_file(rng: np.random.Generator, category: C, index: int, risk_level: str) -> FileRecord:
    size = max(1, int(rng.lognormal(math.log(_MEDIAN_SIZE[category]), _SIZE_SIGMA)))
    age = float(rng.lognormal(math.log(MEDIAN_AGE_DAYS[risk_level]), _AGE_SIGMA))
    path = f"{ROOT}\\{_FOLDER[category]}\\file{index:05d}{_EXTENSION[category]}"
    return _record(rng, path, category, size, age, system=category is C.SYSTEM)


def _make_copies(
    rng: np.random.Generator, originals: list[FileRecord], count: int, risk_level: str
) -> list[FileRecord]:
    sources = [r for r in originals if r.category is not C.SYSTEM] or originals
    copies = []
    for c in range(count):
        src = sources[int(rng.integers(len(sources)))]
        age = float(rng.lognormal(math.log(MEDIAN_AGE_DAYS[risk_level]), _AGE_SIGMA))
        folder = ("Backup", "Downloads", "Desktop\\old", "Documents\\copies")[c % 4]
        path = f"{ROOT}\\{folder}\\copy{c:05d}_{src.name}"
        copy = _record(rng, path, src.category, src.size_bytes, age)
        copies.append(replace(copy, content_hash=f"bench-{src.path}"))
    return copies


def _record(
    rng: np.random.Generator,
    path: str,
    category: C,
    size: int,
    age_days: float,
    *,
    system: bool = False,
) -> FileRecord:
    idle = age_days * float(rng.uniform(0.2, 1.0))
    name = path.rsplit("\\", 1)[1]
    return FileRecord(
        path=path,
        name=name,
        extension=name[name.rfind(".") :].lower() if "." in name else "",
        category=category,
        size_bytes=size,
        created_ts=BENCH_NOW - age_days * DAY,
        modified_ts=BENCH_NOW - age_days * DAY,
        accessed_ts=BENCH_NOW - idle * DAY,
        age_days=age_days,
        idle_days=idle,
        depth=path.count("\\") - ROOT.count("\\") - 1,
        is_hidden=bool(rng.random() < 0.03),
        is_system=system,
        is_readonly=bool(rng.random() < 0.03),
        content_hash=f"bench-{path}",
    )


def _label_duplicates(records: list[FileRecord]) -> list[FileRecord]:
    """Group records by content hash; keep the oldest as original (as sensors.hashing does)."""
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        groups.setdefault(r.content_hash or "", []).append(i)
    multi = [members for members in groups.values() if len(members) > 1]
    out = [replace(r, content_hash=None) for r in records]
    for group_id, members in enumerate(multi, start=1):
        original = min(members, key=lambda i: (records[i].modified_ts, len(records[i].path)))
        for i in members:
            out[i] = replace(records[i], duplicate_group=group_id, is_duplicate=i != original)
    return out
