import pytest

from storageops.config import Config, SafetyConfig
from storageops.core.analysis import analyze
from storageops.models import FileCategory as C
from tests.factories import make_frame, make_record

CONFIG = Config(safety=SafetyConfig(protected_paths=[]))


@pytest.fixture
def analysis():
    frame = make_frame(
        make_record(r"C:\d\old.tmp", size=4_000, category=C.TEMPORARY, age_days=500),
        make_record(r"C:\d\driver.sys", size=8_000, category=C.SYSTEM),
        make_record(r"C:\Users\me\Documents\cv.docx", size=2_000, category=C.DOCUMENT, age_days=1),
    )
    frame.index = [10, 20, 30]  # analyze must not depend on the caller's index
    return analyze(frame, CONFIG)


def test_frames_share_a_range_index(analysis):
    for frame in (analysis.files, analysis.risk, analysis.protection, analysis.heuristic,
                  analysis.tables.benefit):  # fmt: skip
        assert frame.index.tolist() == [0, 1, 2]


def test_candidates_and_best_actions(analysis):
    assert analysis.candidates.tolist() == [True, False, True]
    best = analysis.best_actions()
    assert best["best_action"].tolist()[1] == "KEEP"
    assert best.loc[1, "best_score"] == 0.0
    assert best.loc[0, "best_action"] == "DELETE"
    assert best.loc[0, "best_benefit"] == 4_000
    # the fresh CV is too risky to delete; something milder wins
    assert best.loc[2, "best_action"] in {"ARCHIVE", "COMPRESS"}


def test_action_summary(analysis):
    summary = analysis.action_summary()
    assert list(summary.index) == ["DELETE", "ARCHIVE", "COMPRESS", "MOVE"]
    assert summary.loc["DELETE", "allowed_files"] == 1
    assert summary.loc["DELETE", "potential_bytes"] == 4_000
    assert summary.loc["MOVE", "allowed_files"] == 0


def test_to_frame_is_one_explainable_table(analysis):
    table = analysis.to_frame()
    assert len(table) == 3
    for column in ("path", "risk", "risk_category", "protected", "protection_reason",
                   "benefit_DELETE", "cost_ARCHIVE", "allowed_MOVE", "h_COMPRESS",
                   "best_action"):  # fmt: skip
        assert column in table.columns
    assert table.loc[1, "protection_reason"] == "protected category"


def test_empty_dataset():
    analysis = analyze(make_frame(), CONFIG)
    assert analysis.best_actions().empty
    assert analysis.to_frame().empty
