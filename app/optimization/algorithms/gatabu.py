from __future__ import annotations

from typing import Any, Dict

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .greedy import build_plan as build_greedy_plan
from .ga import _ga_search
from .tabu import _tabu_search


def planned_iterations(payload: Dict[str, Any]) -> int:
    max_iter = int(payload.get("max_iter", 5))
    return 1 + max_iter + max_iter


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state = build_greedy_plan(ctx.problem)
    base_eval = ctx.evaluate(base_state)
    ctx.record(base_state, base_eval, "greedy")

    best_state, best_eval = _ga_search(ctx, start_state=base_state, start_eval=base_eval, record_base=False)
    best_state, best_eval = _tabu_search(ctx, start_state=best_state, start_eval=best_eval, record_base=False)

    return best_state, best_eval


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="gatabu",
        name="GA+Tabu",
        params={
            "max_iter": {"type": "int", "default": 5, "min": 1, "max": 200},
            "time_shift_hours": {"type": "float", "default": 2.0, "min": -24, "max": 24},
            "bucket_shift": {"type": "int", "default": 0, "min": -5, "max": 5},
            "bucket_shift_rate": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
            "qty_jitter_pct": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
            "qty_jitter_rate": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
            "machine_swap_rate": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
            "mold_swap_rate": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
            "mutation_seed": {"type": "int", "default": 0, "min": 0, "max": 999999},
        },
    ),
    planned_iterations=planned_iterations,
    run=run,
)
