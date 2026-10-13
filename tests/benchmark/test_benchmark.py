import math

import pandas as pd
import pytest

from storageops.benchmark import (
    ExperimentSettings,
    InstanceSpec,
    benchmark_suite,
    generate_instance,
    run_benchmark,
    run_experiment,
    scaling_suite,
    summarize,
    weight_sensitivity,
)
from storageops.benchmark.report import markdown_table
from storageops.benchmark.runner import add_quality_columns, default_planners
from storageops.sensors.dataset import COLUMNS


def test_spec_validation_and_name():
    spec = InstanceSpec("medium", 150, 0.3, "high", 0.2, 4)
    assert spec.name == "medium-n150-dup0.3-high-t0.2-s4"
    for bad in (
        {"risk_level": "extreme"},
        {"n_files": 0},
        {"duplicate_ratio": 1.0},
        {"target_fraction": 0.0},
    ):
        with pytest.raises(ValueError):
            InstanceSpec(**{"size_class": "x", "n_files": 10, **bad})


def test_generation_is_deterministic_and_well_formed():
    spec = InstanceSpec("medium", 150, 0.3, "mixed", 0.2, 1)
    a, b = generate_instance(spec), generate_instance(spec)
    pd.testing.assert_frame_equal(a.files, b.files)
    other = generate_instance(InstanceSpec("medium", 150, 0.3, "mixed", 0.2, 2))
    assert not a.files["size_bytes"].equals(other.files["size_bytes"])

    files = a.files
    assert tuple(files.columns) == COLUMNS
    assert len(files) == 150
    assert files["path"].is_unique
    assert (files["size_bytes"] > 0).all()
    assert (files["idle_days"] <= files["age_days"] + 1e-9).all()
    assert a.target_free_bytes > a.free_bytes


def test_duplicates_follow_the_detector_conventions():
    files = generate_instance(InstanceSpec("medium", 200, 0.3, "mixed", 0.2, 0)).files
    copies = files[files["duplicate_group"].notna()]
    assert files["is_duplicate"].sum() == round(0.3 * 200)
    for _, group in copies.groupby("duplicate_group"):
        assert group["size_bytes"].nunique() == 1
        assert group["category"].nunique() == 1
        assert (~group["is_duplicate"]).sum() == 1  # exactly one retained original
        original = group[~group["is_duplicate"]].iloc[0]
        assert original["modified_ts"] == group["modified_ts"].min()


def test_no_duplicates_when_ratio_is_zero():
    files = generate_instance(InstanceSpec("medium", 100, 0.0, "low", 0.2, 0)).files
    assert files["duplicate_group"].isna().all()


def test_risk_level_shifts_the_category_mix_and_age():
    low = generate_instance(InstanceSpec("large", 1500, 0.0, "low", 0.2, 0)).files
    high = generate_instance(InstanceSpec("large", 1500, 0.0, "high", 0.2, 0)).files
    low_share = low["category"].isin(["temporary", "cache", "log"]).mean()
    high_share = high["category"].isin(["temporary", "cache", "log"]).mean()
    assert low_share > 0.35 > 0.15 > high_share
    assert low["age_days"].median() > 3 * high["age_days"].median()
    analysis_low = generate_instance(InstanceSpec("large", 1500, 0.0, "low", 0.2, 0)).analyze()
    analysis_high = generate_instance(InstanceSpec("large", 1500, 0.0, "high", 0.2, 0)).analyze()
    assert analysis_low.risk["risk"].mean() < analysis_high.risk["risk"].mean()


def test_move_is_only_offered_when_enabled():
    off = generate_instance(InstanceSpec("medium", 80, 0.0, "mixed", 0.2, 0))
    on = generate_instance(InstanceSpec("medium", 80, 0.0, "mixed", 0.2, 0, move_enabled=True))
    assert off.move_destination is None
    assert not off.analyze().tables.allowed["MOVE"].any()
    import sys

    if sys.platform == "win32":  # drive letters decide the volume
        assert on.analyze().tables.allowed["MOVE"].any()


def test_suites():
    suite = benchmark_suite(["small", "medium"], seeds=range(2))
    assert len(suite) == 2 * 2 * 3 * 2 * 2
    assert len({s.name for s in suite}) == len(suite)
    scaling = scaling_suite((10, 20), seeds=range(3))
    assert [s.n_files for s in scaling] == [10, 10, 10, 20, 20, 20]


@pytest.fixture(scope="module")
def small_results():
    specs = benchmark_suite(["small"], seeds=range(1), duplicate_ratios=(0.3,))
    return run_benchmark(specs, default_planners(time_limit_s=2.0))


def test_run_benchmark_rows_and_quality(small_results):
    results = small_results
    assert set(results["planner"]) == {
        "greedy", "greedy_heuristic", "bnb", "bnb_heuristic", "exhaustive",
    }  # fmt: skip
    assert len(results) == results["instance"].nunique() * 5
    exact = results[results["planner"].isin(["bnb", "exhaustive"]) & results["is_goal"]]
    assert (exact["excess_cost"].abs() < 1e-9).all()  # both optimal -> they define the best
    assert (results["excess_cost"].dropna() >= -1e-9).all()
    for _, rows in results.groupby("instance"):
        statuses = set(rows.loc[rows["planner"] == "exhaustive", "status"])
        assert statuses <= {"optimal", "infeasible"}


def test_summarize(small_results):
    summary = summarize(small_results, "risk_level")
    assert {"risk_level", "planner", "success_rate", "mean_excess_cost"} <= set(summary.columns)
    bnb = summary[summary["planner"] == "bnb"]
    assert (bnb["mean_excess_cost"].fillna(0) == 0).all()


def test_quality_columns_handle_infeasible_instances():
    rows = pd.DataFrame(
        {
            "instance": ["a", "a", "b"],
            "planner": ["x", "y", "x"],
            "is_goal": [True, True, False],
            "total_cost": [2.0, 3.0, 0.0],
            "proven_optimal": [True, False, False],
            "freed_bytes": [110, 150, 0],
            "required_bytes": [100, 100, 100],
        }
    )
    out = add_quality_columns(rows)
    assert out["best_cost"].tolist()[:2] == [2.0, 2.0]
    assert out["excess_cost"].tolist()[:2] == pytest.approx([0.0, 0.5])
    assert math.isnan(out["excess_cost"].iloc[2])
    assert out["overshoot"].tolist()[:2] == pytest.approx([0.1, 0.5])


def test_weight_sensitivity_includes_defaults():
    specs = [InstanceSpec("medium", 60, 0.3, "mixed", 0.2, 0)]
    table = weight_sensitivity(specs, values=(0.0, 1.0), time_limit_s=0.5)
    assert set(table["weight"]) == {
        "weight_benefit", "weight_inactivity", "weight_duplicate", "weight_risk",
    }  # fmt: skip
    assert table.groupby("weight")["is_default"].sum().eq(1).all()
    assert (table["greedy_excess_cost"].dropna() >= -1e-9).all()


def test_weight_sensitivity_on_infeasible_instances_is_empty_but_typed():
    table = weight_sensitivity([InstanceSpec("medium", 60, 0.3, "mixed", 0.3, 0)], values=(0.0,))
    assert table.empty and "greedy_excess_cost" in table.columns


def test_markdown_table():
    frame = pd.DataFrame({"a": [1, 2], "b": [0.5, float("nan")], "c": [True, False]})
    lines = markdown_table(frame).splitlines()
    assert lines[0] == "| a | b | c |"
    assert lines[2] == "| 1 | 0.5 | yes |"
    assert lines[3] == "| 2 | - | no |"


def test_quick_experiment_writes_everything(tmp_path):
    settings = ExperimentSettings(
        size_classes=("small",),
        seeds=1,
        time_limit_s=0.5,
        scaling_sizes=(10, 20),
        sensitivity_instances=1,
        sensitivity_values=(0.0, 1.0),
    )
    written = run_experiment(tmp_path, settings, log=lambda _: None)
    for key in ("results", "scaling", "sensitivity", "summary", "fig_quality", "fig_time",
                "fig_nodes", "fig_sens_quality", "fig_sens_nodes"):  # fmt: skip
        assert written[key].exists() and written[key].stat().st_size > 0
    summary = written["summary"].read_text(encoding="utf-8")
    assert "## By size class" in summary and "## Scaling: median planning time (ms)" in summary


def test_every_spec_gets_its_own_random_stream():
    suite = benchmark_suite(["small"], seeds=range(3))
    fingerprints = {tuple(generate_instance(s).files["size_bytes"]) for s in suite}
    assert len(fingerprints) == len(suite)


def test_summary_action_counts_ignore_failed_plans():
    rows = add_quality_columns(
        pd.DataFrame(
            {
                "instance": ["a", "b"], "size_class": ["s", "s"], "planner": ["x", "x"],
                "is_goal": [True, False], "total_cost": [1.0, 0.0], "proven_optimal": [False] * 2,
                "freed_bytes": [10, 5], "required_bytes": [10, 10], "actions": [4, 400],
                "elapsed_s": [0.1, 0.1], "nodes_generated": [1, 1], "limit_reached": [False] * 2,
            }
        )
    )  # fmt: skip
    assert summarize(rows)["mean_actions"].iloc[0] == 4
