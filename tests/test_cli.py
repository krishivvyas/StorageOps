import pandas as pd
import pytest

from storageops.cli import main


def test_scan_prints_report_and_saves(sample_tree, tmp_path, capsys):
    out = tmp_path / "scan.csv"
    assert main(["scan", "--yes", str(sample_tree), "-o", str(out), "-q"]) == 0

    stdout = capsys.readouterr().out
    assert "Scanned 8 files" in stdout
    assert "Duplicates: 1 groups, 2 redundant copies" in stdout
    assert out.exists() and (tmp_path / "scan.meta.json").exists()


def test_scan_missing_root_returns_error(tmp_path, capsys):
    assert main(["scan", "--yes", str(tmp_path / "missing"), "-q"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_analyze_folder(sample_tree, tmp_path, capsys):
    out = tmp_path / "analysis.csv"
    assert main(["analyze", "--yes", str(sample_tree), "--free", "1KB", "-o", str(out), "-q"]) == 0
    stdout = capsys.readouterr().out
    assert "must free 1.0 KB" in stdout
    assert "Search problem:" in stdout and "feasible" in stdout
    assert out.exists()


def test_analyze_saved_dataset_reports_infeasible_target(sample_tree, tmp_path, capsys):
    dataset = tmp_path / "scan.csv"
    main(["scan", "--yes", str(sample_tree), "-o", str(dataset), "-q"])
    capsys.readouterr()
    assert main(["analyze", "--yes", str(dataset), "--free", "10TB"]) == 0
    assert "INFEASIBLE" in capsys.readouterr().out


def test_analyze_rejects_bad_size(sample_tree):
    with pytest.raises(SystemExit):
        main(["analyze", "--yes", str(sample_tree), "--free", "lots"])


def test_plan_compare_and_save(sample_tree, tmp_path, capsys):
    out = tmp_path / "plan.csv"
    args = ["plan", "--yes", str(sample_tree), "--free", "4KB", "--compare", "-o", str(out), "-q"]
    assert main(args) == 0
    stdout = capsys.readouterr().out
    assert "Planner comparison:" in stdout
    assert "Plan from branch_and_bound: optimal" in stdout
    assert "Nothing has been changed" in stdout
    plan = pd.read_csv(out)
    assert list(plan.columns) == ["path", "action", "size_bytes", "benefit_bytes", "cost", "risk"]
    assert plan["benefit_bytes"].sum() >= 4096


def test_plan_reports_infeasible_target(sample_tree, capsys):
    assert (
        main(["plan", "--yes", str(sample_tree), "--free", "10TB", "--planner", "greedy", "-q"])
        == 0
    )
    assert "no plan found" in capsys.readouterr().out
    assert main(["plan", "--yes", str(sample_tree), "--free", "10TB", "-q"]) == 0
    assert "cannot be reached" in capsys.readouterr().out
