from storageops.cli import main


def test_scan_prints_report_and_saves(sample_tree, tmp_path, capsys):
    out = tmp_path / "scan.csv"
    assert main(["scan", str(sample_tree), "-o", str(out), "-q"]) == 0

    stdout = capsys.readouterr().out
    assert "Scanned 8 files" in stdout
    assert "Duplicates: 1 groups, 2 redundant copies" in stdout
    assert out.exists() and (tmp_path / "scan.meta.json").exists()


def test_scan_missing_root_returns_error(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "missing"), "-q"]) == 2
    assert "does not exist" in capsys.readouterr().err
