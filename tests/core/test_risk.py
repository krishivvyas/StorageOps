import pytest

from storageops.config import RiskConfig
from storageops.core.risk import RISK_COLUMNS, assess_risk
from storageops.models import FileCategory as C
from tests.factories import make_frame, make_record


def risk_of(*records, config=None):
    return assess_risk(make_frame(*records), config)


def test_old_temp_download_is_low_risk_and_fresh_document_is_high():
    result = risk_of(
        make_record(r"C:\Users\me\Downloads\old.tmp", category=C.TEMPORARY, age_days=800),
        make_record(r"C:\Users\me\Documents\thesis.docx", category=C.DOCUMENT, age_days=0),
    )
    old_temp, thesis = result["risk"]
    assert old_temp == pytest.approx(0.0, abs=1e-3)  # 0.6*0.05 + ~0 - 0.15 relief -> clipped
    assert thesis == pytest.approx(1.0)  # 0.48 + 0.25 + 0.15 + 0.2 boost -> clipped to 1


def test_components_follow_half_lives():
    result = risk_of(
        make_record(r"C:\data\a.bin", age_days=0, idle_days=0),
        make_record(r"C:\data\b.bin", age_days=90, idle_days=180),
    )
    assert list(result.columns) == list(RISK_COLUMNS)
    assert result["risk_recency"].tolist() == pytest.approx([1.0, 0.5])
    assert result["risk_usage"].tolist() == pytest.approx([1.0, 0.25])
    # other: base 0.5 -> 0.6*0.5 + 0.25*0.5 + 0.15*0.25
    assert result["risk"].iloc[1] == pytest.approx(0.3 + 0.125 + 0.0375)


def test_duplicates_are_discounted():
    result = risk_of(
        make_record(r"C:\data\a.pdf", category=C.DOCUMENT, duplicate_group=1),
        make_record(r"C:\data\b.pdf", category=C.DOCUMENT),
    )
    dup, single = result["risk"]
    assert dup == pytest.approx(single * 0.25)
    assert result["duplicate_factor"].tolist() == [0.25, 1.0]


def test_flags_and_location_adjust_risk():
    plain, hidden_ro, important = risk_of(
        make_record(r"C:\data\a.bin"),
        make_record(r"C:\data\b.bin", hidden=True, readonly=True),
        make_record(r"C:\Users\me\Desktop\c.bin"),
    )["risk"]
    assert hidden_ro == pytest.approx(plain + 0.15)
    assert important == pytest.approx(plain + 0.2)


def test_folder_keywords_match_folders_not_file_names():
    named, inside = risk_of(
        make_record(r"C:\data\documents.txt"),
        make_record(r"C:\data\Documents\x.txt"),
    )["risk_location"]
    assert named == 0.0 and inside == pytest.approx(0.2)


def test_category_table_can_be_overridden():
    config = RiskConfig(category_base={"video": 0.0})
    assert config.category_base["document"] == 0.8  # untouched defaults survive
    record = make_record(r"C:\v\m.mkv", category=C.VIDEO, age_days=10_000)
    (risk,) = risk_of(record, config=config)["risk"]
    assert risk == pytest.approx(0.0, abs=1e-9)


def test_empty_frame():
    assert assess_risk(make_frame()).empty
