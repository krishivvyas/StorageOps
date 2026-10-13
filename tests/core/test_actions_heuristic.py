import math
import sys

import numpy as np
import pandas as pd
import pytest

from storageops.config import HeuristicConfig
from storageops.core.actions import ACTIONS, build_action_tables
from storageops.core.heuristic import heuristic_scores
from storageops.models import Action
from storageops.models import FileCategory as C
from tests.factories import make_frame, make_record


def tables_for(frame, risk, protected=None, **kwargs):
    risk = pd.Series(risk, index=frame.index, dtype=float)
    protected = pd.Series(protected or [False] * len(frame), index=frame.index)
    return build_action_tables(frame, risk, protected, max_action_risk=0.6, **kwargs)


def test_action_order_starts_with_keep():
    assert ACTIONS[0] is Action.KEEP and set(ACTIONS) == set(Action)


def test_benefits_and_costs():
    frame = make_frame(make_record(r"C:\d\a.docx", size=1000, category=C.DOCUMENT))
    t = tables_for(frame, [0.5])
    row = t.benefit.iloc[0]
    assert row["KEEP"] == 0
    assert row["DELETE"] == 1000
    assert row["ARCHIVE"] == 400  # ratio 0.6
    assert row["COMPRESS"] == 240  # 400 * 0.6 in-place efficiency
    assert row["MOVE"] == 0  # no destination

    cost = t.cost.iloc[0]
    assert cost["KEEP"] == 0
    assert cost["DELETE"] == pytest.approx(1.0 * 0.5 + 0.02)
    assert cost["ARCHIVE"] == pytest.approx(0.4 * 0.5 + 0.04)
    assert cost["COMPRESS"] == pytest.approx(0.1 * 0.5 + 0.03)
    assert math.isinf(cost["MOVE"])


def test_risk_threshold_blocks_only_drastic_actions():
    frame = make_frame(make_record(r"C:\d\a.docx", category=C.DOCUMENT))
    allowed = tables_for(frame, [0.9]).allowed.iloc[0]
    assert not allowed["DELETE"]  # 0.9 > 0.6
    assert allowed["ARCHIVE"] and allowed["COMPRESS"]  # 0.36 and 0.09
    assert allowed["KEEP"]


def test_protected_files_may_only_be_kept():
    frame = make_frame(make_record(r"C:\d\a.docx", category=C.DOCUMENT))
    t = tables_for(frame, [0.0], protected=[True])
    assert t.allowed.iloc[0].tolist() == [True, False, False, False, False]
    assert np.isinf(t.cost.iloc[0, 1:]).all()


def test_compression_is_not_offered_for_incompressible_media():
    frame = make_frame(make_record(r"C:\v\m.mp4", size=10_000, category=C.VIDEO))
    allowed = tables_for(frame, [0.1]).allowed.iloc[0]
    assert not allowed["ARCHIVE"] and not allowed["COMPRESS"] and allowed["DELETE"]


def test_zero_byte_files_have_no_removal_options():
    frame = make_frame(make_record(r"C:\d\empty.txt", size=0))
    assert tables_for(frame, [0.0]).allowed.iloc[0].tolist() == [True, False, False, False, False]


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters")
def test_move_requires_a_destination_on_another_volume():
    frame = make_frame(
        make_record(r"C:\d\a.bin", size=500), make_record(r"D:\already\there.bin", size=500)
    )
    t = tables_for(frame, [0.1, 0.1], move_destination=r"D:\Backup")
    assert t.allowed["MOVE"].tolist() == [True, False]
    assert t.benefit["MOVE"].tolist() == [500, 0]


def test_heuristic_keep_is_zero_and_disallowed_is_minus_infinity():
    frame = make_frame(make_record(r"C:\d\a.docx", category=C.DOCUMENT))
    risk = pd.Series([0.9])
    t = tables_for(frame, [0.9])
    h = heuristic_scores(frame, risk, t)
    assert h.iloc[0]["KEEP"] == 0.0
    assert h.iloc[0]["DELETE"] == -np.inf and h.iloc[0]["MOVE"] == -np.inf
    assert np.isfinite(h.iloc[0]["ARCHIVE"])


def test_heuristic_prefers_big_old_duplicates_and_penalises_risk():
    frame = make_frame(
        make_record(r"C:\d\small.bin", size=1_000, age_days=30),
        make_record(r"C:\d\big.bin", size=1_000_000, age_days=30),
        make_record(r"C:\d\big_old.bin", size=1_000_000, age_days=900),
        make_record(r"C:\d\big_old_dup.bin", size=1_000_000, age_days=900, duplicate_group=1),
        make_record(r"C:\d\big_old_risky.bin", size=1_000_000, age_days=900),
    )
    risk = pd.Series([0.2, 0.2, 0.2, 0.2, 0.5])
    h = heuristic_scores(frame, risk, tables_for(frame, risk.tolist()))["DELETE"].tolist()
    small, big, big_old, big_old_dup, big_old_risky = h
    assert small < big < big_old < big_old_dup
    assert big_old_risky < big_old


def test_heuristic_benefit_term_is_log_normalised():
    frame = make_frame(make_record(r"C:\d\a.bin", size=99), make_record(r"C:\d\b.bin", size=9999))
    only_benefit = HeuristicConfig(weight_benefit=1, weight_inactivity=0, weight_duplicate=0,
                                   weight_risk=0)  # fmt: skip
    t = tables_for(frame, [0.0, 0.0])
    h = heuristic_scores(frame, pd.Series([0.0, 0.0]), t, only_benefit)["DELETE"].tolist()
    assert h == pytest.approx([np.log(100) / np.log(10000), 1.0])
