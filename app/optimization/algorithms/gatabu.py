from __future__ import annotations

from typing import Any, Dict

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .ga import _ga_search
from .tabu import _tabu_search
from .utils import select_initial_state


def planned_iterations(payload: Dict[str, Any]) -> int:
    max_iter = int(payload.get("max_iter", 5))
    return 1 + max_iter + max_iter


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state, base_eval, base_label = select_initial_state(ctx)
    ctx.record(base_state, base_eval, base_label)

    best_state, best_eval = _ga_search(ctx, start_state=base_state, start_eval=base_eval, record_base=False)
    best_state, best_eval = _tabu_search(ctx, start_state=best_state, start_eval=best_eval, record_base=False)

    return best_state, best_eval


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="gatabu",
        name="GA+Tabu (Sequential)",
        desc="Önce GA çalıştırılır, ardından en iyi sonuç üzerinde tabu uygulanır.",
        params={
            "max_iter": {"type": "int", "default": 50, "min": 1, "max": 200},
            "population_size": {"type": "int", "default": 20, "min": 2, "max": 100},
            "crossover_rate": {"type": "float", "default": 0.8, "min": 0.0, "max": 1.0},
            "selection_tournament_k": {"type": "int", "default": 3, "min": 2, "max": 10},
            "early_stop_patience": {"type": "int", "default": 0, "min": 0, "max": 200},
            "time_shift_hours": {"type": "float", "default": 0.0, "min": -24, "max": 24},
            "bucket_shift": {"type": "int", "default": 1, "min": -5, "max": 5},
            "bucket_shift_rate": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0},
            "qty_jitter_pct": {"type": "float", "default": 0.05, "min": 0.0, "max": 1.0},
            "qty_jitter_rate": {"type": "float", "default": 0.35, "min": 0.0, "max": 1.0},
            "machine_swap_rate": {"type": "float", "default": 0.15, "min": 0.0, "max": 1.0},
            "mold_swap_rate": {"type": "float", "default": 0.15, "min": 0.0, "max": 1.0},
            "mutation_seed": {"type": "int", "default": 42, "min": 0, "max": 999999},
        },
    ),
    planned_iterations=planned_iterations,
    run=run,
)
