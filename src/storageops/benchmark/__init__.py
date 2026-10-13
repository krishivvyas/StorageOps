"""Phase 4 - Benchmarking: synthetic instances, experiments, figures and reports."""

from storageops.benchmark.generator import BenchmarkInstance, generate_instance
from storageops.benchmark.profiles import (
    SIZE_CLASSES,
    InstanceSpec,
    benchmark_suite,
    scaling_suite,
)
from storageops.benchmark.report import ExperimentSettings, run_experiment
from storageops.benchmark.runner import default_planners, run_benchmark, summarize
from storageops.benchmark.sensitivity import weight_sensitivity

__all__ = [
    "SIZE_CLASSES",
    "BenchmarkInstance",
    "ExperimentSettings",
    "InstanceSpec",
    "benchmark_suite",
    "default_planners",
    "generate_instance",
    "run_benchmark",
    "run_experiment",
    "scaling_suite",
    "summarize",
    "weight_sensitivity",
]
