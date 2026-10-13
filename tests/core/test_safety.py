import sys

import pytest

from storageops.config import SafetyConfig
from storageops.core.safety import assess_protection, protected_prefixes
from storageops.models import FileCategory as C
from tests.factories import make_frame, make_record

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")


def reasons(*records, **cfg):
    return assess_protection(make_frame(*records), SafetyConfig(**cfg))[
        "protection_reason"
    ].tolist()


@windows_only
def test_protected_location_is_case_insensitive_and_respects_folder_boundaries():
    result = reasons(
        make_record(r"C:\Windows\notes.txt"),
        make_record(r"c:\WINDOWS\Temp\x.log"),
        make_record(r"C:\Windows2\notes.txt"),
        protected_paths=[r"C:\Windows"],
    )
    assert result == ["protected location", "protected location", ""]


@windows_only
def test_environment_variables_are_expanded_and_undefined_ones_ignored(monkeypatch):
    monkeypatch.setenv("STORAGEOPS_GUARDED", r"D:\Guarded")
    monkeypatch.delenv("STORAGEOPS_UNDEFINED", raising=False)
    config = SafetyConfig(protected_paths=["%STORAGEOPS_GUARDED%", "%STORAGEOPS_UNDEFINED%"])
    assert protected_prefixes(config) == ("d:\\guarded\\",)


def test_attribute_category_and_name_rules():
    result = reasons(
        make_record(r"C:\data\a.bin", system=True),
        make_record(r"C:\data\driver.sys", category=C.SYSTEM),
        make_record(r"C:\Users\me\.ssh\Server.PEM"),
        make_record(r"C:\Users\me\.ssh\id_rsa"),
        make_record(r"C:\data\holiday.jpg", category=C.IMAGE),
        protected_paths=[],
    )
    assert result == [
        "system file attribute",
        "protected category",
        "protected name pattern",
        "protected name pattern",
        "",
    ]


def test_rules_can_be_disabled():
    result = reasons(
        make_record(r"C:\data\a.bin", system=True, category=C.SYSTEM),
        protected_paths=[],
        protect_system_files=False,
        protected_categories=[],
        protected_name_patterns=[],
    )
    assert result == [""]


def test_first_matching_rule_gives_the_reason(tmp_path):
    result = reasons(
        make_record(str(tmp_path / "keys" / "a.pem"), system=True),
        protected_paths=[str(tmp_path)],
    )
    assert result == ["protected location"]


def test_unknown_protected_category_is_rejected():
    with pytest.raises(ValueError):
        SafetyConfig(protected_categories=["secret"])
