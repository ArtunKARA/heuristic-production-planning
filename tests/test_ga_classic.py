from __future__ import annotations

import copy
import random
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.optimization.algorithms.base import AlgorithmContext
from app.optimization.algorithms.ga import (
    _ga_run_generation,
    _ga_search,
    _mutation_config,
    _tournament_select,
    _uniform_lot_crossover,
    run as ga_run,
)
from app.optimization.algorithms.ga_tabu_inline import (
    planned_iterations as inline_planned_iterations,
    run as ga_tabu_inline_run,
)
from app.optimization.algorithms.ga_tabu_topk import (
    planned_iterations as topk_planned_iterations,
    run as ga_tabu_topk_run,
)


def _base_state() -> Dict[str, Any]:
    return {
        "lots": [
            {
                "lot_id": "L1",
                "product_code": "P1",
                "process_code": "AP300",
                "time_bucket_id": "B1",
                "qty": 8.0,
                "process_start_time": "2025-01-01T00:00:00",
                "process_end_time": "2025-01-01T01:00:00",
                "assigned_resources": {"machine": 1, "mold": "M1"},
            },
            {
                "lot_id": "L2",
                "product_code": "P1",
                "process_code": "AP300",
                "time_bucket_id": "B1",
                "qty": 9.0,
                "process_start_time": "2025-01-01T01:00:00",
                "process_end_time": "2025-01-01T02:00:00",
                "assigned_resources": {"machine": 1, "mold": "M1"},
            },
            {
                "lot_id": "L3",
                "product_code": "P1",
                "process_code": "AP300",
                "time_bucket_id": "B1",
                "qty": 10.0,
                "process_start_time": "2025-01-01T02:00:00",
                "process_end_time": "2025-01-01T03:00:00",
                "assigned_resources": {"machine": 1, "mold": "M1"},
            },
        ]
    }


def _problem() -> Dict[str, Any]:
    return {
        "time_buckets": [
            {"id": "B1", "start_date": "2025-01-01", "end_date": "2025-01-07"},
        ],
        "resources": {
            "machines": [
                {"id": 1, "process_code": "AP300"},
                {"id": 2, "process_code": "AP300"},
            ],
            "molds": [
                {"code": "M1", "process_code": "AP300", "eye": 1},
                {"code": "M2", "process_code": "AP300", "eye": 1},
            ],
        },
        "compatibility": {
            "machine_mold_pairs": [
                {"machine_id": 1, "mold_code": "M1", "process_code": "AP300"},
                {"machine_id": 1, "mold_code": "M2", "process_code": "AP300"},
                {"machine_id": 2, "mold_code": "M1", "process_code": "AP300"},
                {"machine_id": 2, "mold_code": "M2", "process_code": "AP300"},
            ],
            "product_molds": [
                {"product_code": "P1", "process_code": "AP300", "allowed_molds": ["M1", "M2"]},
            ],
        },
    }


def _evaluate(state: Dict[str, Any]) -> Dict[str, Any]:
    total = 0.0
    for lot in state.get("lots", []):
        ar = lot.get("assigned_resources", {}) or {}
        machine_penalty = 0.0 if ar.get("machine") == 1 else 1.0
        mold_penalty = 0.0 if ar.get("mold") == "M1" else 0.1
        total += float(lot.get("qty", 0.0)) + machine_penalty + mold_penalty
    return {"feasible": True, "total_cost": total, "total_score": total}


def _make_ctx(payload: Dict[str, Any], *, frame_state: Dict[str, Any] | None = None):
    records: List[Dict[str, Any]] = []

    def _record(state_dict: Dict[str, Any], eval_res: Dict[str, Any], label: str) -> None:
        records.append(
            {
                "label": label,
                "state": copy.deepcopy(state_dict),
                "eval": dict(eval_res),
            }
        )

    ctx = AlgorithmContext(
        problem=_problem(),
        scenario={},
        payload=dict(payload),
        frame_state=copy.deepcopy(frame_state or {}),
        evaluate=_evaluate,
        record=_record,
        build_greedy_plan=lambda _problem: copy.deepcopy(_base_state()),
    )
    return ctx, records


def test_ga_records_one_entry_per_generation():
    ctx, records = _make_ctx(
        {
            "max_iter": 3,
            "population_size": 6,
            "crossover_rate": 0.8,
            "selection_tournament_k": 3,
            "mutation_seed": 7,
        }
    )
    ga_run(ctx)
    labels = [r["label"] for r in records]
    assert labels[0] == "greedy"
    assert labels[1:] == ["ga-1", "ga-2", "ga-3"]
    assert ctx.eval_calls_total > len(records)


def test_uniform_lot_crossover_mixes_parent_lots():
    parent_a = {
        "lots": [
            {"lot_id": f"A{i}", "qty": 1.0, "assigned_resources": {"machine": 1, "mold": "M1"}}
            for i in range(10)
        ]
    }
    parent_b = {
        "lots": [
            {"lot_id": f"B{i}", "qty": 2.0, "assigned_resources": {"machine": 2, "mold": "M2"}}
            for i in range(10)
        ]
    }
    child = _uniform_lot_crossover(parent_a, parent_b, random.Random(42))
    machines = {lot["assigned_resources"]["machine"] for lot in child["lots"]}
    assert machines == {1, 2}
    assert len(child["lots"]) == 10


def test_tournament_selection_picks_best_when_sampling_all():
    population = [
        ({"lots": []}, {"feasible": True, "total_score": 10.0}),
        ({"lots": []}, {"feasible": True, "total_score": 1.0}),
        ({"lots": []}, {"feasible": True, "total_score": 5.0}),
    ]
    selected = _tournament_select(population, tournament_k=3, rng=random.Random(1))
    assert selected[1]["total_score"] == 1.0


def test_elite_is_preserved_by_generation_step():
    ctx, records = _make_ctx({"mutation_seed": 13})
    mutation_cfg = _mutation_config({"mutation_seed": 13})
    ga_cfg = {"population_size": 3, "crossover_rate": 0.0, "selection_tournament_k": 2}

    elite_state = {"lots": [{"lot_id": "E", "qty": 1.0, "assigned_resources": {"machine": 1, "mold": "M1"}}]}
    weak_state = {"lots": [{"lot_id": "W", "qty": 8.0, "assigned_resources": {"machine": 1, "mold": "M1"}}]}
    worst_state = {"lots": [{"lot_id": "X", "qty": 12.0, "assigned_resources": {"machine": 1, "mold": "M1"}}]}
    population = [
        (elite_state, _evaluate(elite_state)),
        (weak_state, _evaluate(weak_state)),
        (worst_state, _evaluate(worst_state)),
    ]

    next_population, _ = _ga_run_generation(
        ctx,
        population=population,
        generation_no=1,
        mutation_cfg=mutation_cfg,
        ga_cfg=ga_cfg,
        rng=random.Random(5),
        label_prefix="ga-test",
        record_generation=True,
    )

    assert next_population[0][0]["lots"][0]["lot_id"] == "E"
    assert records[-1]["label"] == "ga-test-1"


def test_early_stop_patience_halts_search():
    ctx, records = _make_ctx(
        {
            "max_iter": 10,
            "population_size": 6,
            "crossover_rate": 0.8,
            "selection_tournament_k": 3,
            "early_stop_patience": 2,
            "mutation_seed": 21,
        }
    )
    start_state = copy.deepcopy(_base_state())
    start_eval = _evaluate(start_state)
    _ga_search(
        ctx,
        start_state=start_state,
        start_eval=start_eval,
        record_base=True,
        base_label="greedy",
    )
    labels = [r["label"] for r in records]
    assert labels == ["greedy", "ga-1", "ga-2"]


def test_ga_tabu_inline_records_summary_and_ignores_tabu_rate():
    payload = {
        "max_iter": 3,
        "population_size": 6,
        "crossover_rate": 0.8,
        "selection_tournament_k": 3,
        "tabu_iter": 1,
        "mutation_seed": 33,
    }

    ctx_a, records_a = _make_ctx({**payload, "tabu_rate": 0.0})
    _state_a, eval_a = ga_tabu_inline_run(ctx_a)
    labels_a = [r["label"] for r in records_a]

    ctx_b, records_b = _make_ctx({**payload, "tabu_rate": 1.0})
    _state_b, eval_b = ga_tabu_inline_run(ctx_b)
    labels_b = [r["label"] for r in records_b]

    expected = [
        "greedy",
        "ga-inline-1",
        "tabu-inline-1",
        "ga-inline-2",
        "tabu-inline-2",
        "ga-inline-3",
        "tabu-inline-3",
    ]
    assert labels_a == expected
    assert labels_b == expected
    assert eval_a["total_score"] == eval_b["total_score"]
    assert inline_planned_iterations(payload) == len(expected)


def test_ga_tabu_topk_records_summary_per_generation():
    payload = {
        "max_iter": 2,
        "population_size": 6,
        "crossover_rate": 0.8,
        "selection_tournament_k": 3,
        "top_k": 2,
        "tabu_iter": 1,
        "mutation_seed": 17,
    }
    ctx, records = _make_ctx(payload)
    ga_tabu_topk_run(ctx)
    labels = [r["label"] for r in records]
    assert labels == ["greedy", "ga-topk-1", "tabu-topk-1", "ga-topk-2", "tabu-topk-2"]
    assert topk_planned_iterations(payload) == len(labels)
