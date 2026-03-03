from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


def _load_runner_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "Doc" / "api_benchmark_runner.py"
    spec = importlib.util.spec_from_file_location("api_benchmark_runner", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flatten_param_groups():
    mod = _load_runner_module()
    nested = {
        "core": {"max_iter": 50},
        "hho": {"hho_hawks": 12},
        "other": {"group": {"mutation_seed": 42}},
    }
    flat = mod._flatten_param_groups(nested)
    assert flat["max_iter"] == 50
    assert flat["hho_hawks"] == 12
    assert flat["mutation_seed"] == 42


def test_merge_runtime_settings_new_schema():
    mod = _load_runner_module()
    args = argparse.Namespace(
        api_base_url="http://127.0.0.1:8000",
        input="Doc/SampleData/example_input.json",
        outdir="Doc/api_benchmark_outputs",
        iterations="10,20",
        runs=2,
        metric="total_score",
        methods="",
        exclude_methods="greedy",
        timeout=90.0,
        seed_offset=0,
        run_name="",
        resume=False,
        resume_dir="",
        generate_plots=False,
        plot_dpi=300,
    )
    ui_cfg = {
        "api": {"base_url": "http://localhost:9999", "timeout_seconds": 123},
        "input_output": {"input_file": "x.json", "output_dir": "out"},
        "benchmark_plan": {
            "run_name": "paper_run",
            "resume": True,
            "iteration_list": [5, 10],
            "runs_per_iteration": 3,
            "primary_metric": "total_score",
            "seed_offset": 11,
            "generate_plots": True,
            "plot_dpi": 600,
            "table_metrics": ["mean", "std_dev"],
        },
        "algorithm_selection": {"include_methods": ["ga"], "exclude_methods": ["greedy"]},
        "parameters": {
            "ui_global_parameters": {"core": {"max_iter": 100}, "hho": {"hho_hawks": 8}},
            "common_request_overrides": {"bucket_shift": 1},
            "method_specific_overrides": {"ga": {"population_size": 20}},
        },
    }
    settings = mod._merge_runtime_settings(args, ui_cfg)
    assert settings["api_base_url"] == "http://localhost:9999"
    assert settings["input"] == "x.json"
    assert settings["outdir"] == "out"
    assert settings["iterations"] == [5, 10]
    assert settings["runs"] == 3
    assert settings["run_name"] == "paper_run"
    assert settings["resume"] is True
    assert settings["ui_params"]["max_iter"] == 100
    assert settings["ui_params"]["hho_hawks"] == 8
    assert settings["common_params"]["bucket_shift"] == 1
    assert settings["method_params"]["ga"]["population_size"] == 20
    assert settings["plot_dpi"] == 600
    assert settings["table_metrics"] == ["mean", "std_dev"]


def test_completed_job_keys_from_rows():
    mod = _load_runner_module()
    rows = [
        {"job_key": "ga|10|1", "status": "ok"},
        {"method": "ga", "n_iter": "10", "run": "2", "status": "error"},
        {"method": "ga", "n_iter": "10", "run": "3", "status": "pending"},
    ]
    keys = mod._completed_job_keys(rows)
    assert "ga|10|1" in keys
    assert "ga|10|2" in keys
    assert "ga|10|3" not in keys


def test_wide_metric_table():
    mod = _load_runner_module()
    summary_rows = [
        {"method": "ga", "n_iter": 10, "mean": 1.1},
        {"method": "ga", "n_iter": 20, "mean": 1.2},
        {"method": "hho", "n_iter": 10, "mean": 0.9},
    ]
    fields, rows = mod._wide_metric_table(summary_rows, "mean")
    assert fields == ["method", "n_10", "n_20"]
    by_method = {r["method"]: r for r in rows}
    assert by_method["ga"]["n_10"] == 1.1
    assert by_method["ga"]["n_20"] == 1.2
    assert by_method["hho"]["n_10"] == 0.9


def test_outer_iter_from_label():
    mod = _load_runner_module()
    assert mod._outer_iter_from_label("greedy", 1) == 0
    assert mod._outer_iter_from_label("state", 1) == 0
    assert mod._outer_iter_from_label("ga-7", 3) == 7
    assert mod._outer_iter_from_label("tabu-inline-5-2", 20) == 5
    assert mod._outer_iter_from_label("unknown", 9) == 9


def test_quality_feasibility_efficiency_rows():
    mod = _load_runner_module()
    by_n, by_method = mod._quality_feasibility_efficiency_rows(
        [
            {
                "method": "ga",
                "n_iter": 10,
                "status": "ok",
                "best_total_score": 100.0,
                "best_total_cost": 60.0,
                "best_hard_total": 0.0,
                "eval_calls_total": 50,
                "elapsed_sec": 10.0,
            },
            {
                "method": "ga",
                "n_iter": 10,
                "status": "ok",
                "best_total_score": 120.0,
                "best_total_cost": 70.0,
                "best_hard_total": 2.0,
                "eval_calls_total": 60,
                "elapsed_sec": 12.0,
            },
            {
                "method": "ga",
                "n_iter": 10,
                "status": "error",
                "best_total_score": 999.0,
            },
        ]
    )
    assert len(by_n) == 1
    row = by_n[0]
    assert row["method"] == "ga"
    assert row["n_iter"] == 10
    assert row["runs_ok"] == 2
    assert row["feasible_rate"] == 0.5
    assert row["median_best_hard_total"] == 1.0
    assert len(by_method) == 1
    assert by_method[0]["method"] == "ga"
