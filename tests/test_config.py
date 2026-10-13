from pathlib import Path

import pytest

from storageops.config import Config, load_config

DEFAULT_TOML = Path(__file__).resolve().parents[1] / "config" / "default.toml"


def test_defaults_without_file():
    assert load_config() == Config()


def test_shipped_default_toml_matches_builtin_defaults():
    assert load_config(DEFAULT_TOML) == Config()


def test_overrides_and_normalisation(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[scanner]\nmax_depth = 3\nexcluded_dir_names = ["Node_Modules"]\n'
        'excluded_extensions = ["TMP", ".log"]\n[hashing]\npartial_bytes = 4096\n'
    )
    cfg = load_config(path)
    assert cfg.scanner.max_depth == 3
    assert cfg.scanner.excluded_dir_names == frozenset({"node_modules"})
    assert cfg.scanner.excluded_extensions == frozenset({".tmp", ".log"})
    assert cfg.hashing.partial_bytes == 4096


@pytest.mark.parametrize(
    "text",
    [
        "[scaner]\nmax_depth = 1\n",  # unknown section
        "[scanner]\nmax_dept = 1\n",  # unknown key
        "[scanner]\nmax_depth = -1\n",
        '[hashing]\nalgorithm = "not-a-hash"\n',
        "[hashing]\nmin_size_bytes = 0\n",
    ],
)
def test_invalid_config_is_rejected(tmp_path, text):
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_config(path)
