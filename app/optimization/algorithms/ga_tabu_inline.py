from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .ga import _mutate_state, _mutation_config
from .tabu import _tabu_search
from .utils import is_better, select_initial_state, total_score


def _rank_key(eval_res: Dict[str, Any]) -> Tuple[int, float]:
    feasible = bool(eval_res.get("feasible", False))
    return (0 if feasible else 1, total_score(eval_res))


def planned_iterations(payload: Dict[str, Any]) -> int:
    max_iter = int(payload.get("max_iter", 5))
    tabu_iter = int(payload.get("tabu_iter", 0))
    tabu_rate = float(payload.get("tabu_rate", 0.0))
    extra = int(round(max_iter * tabu_rate * tabu_iter)) if tabu_iter > 0 and tabu_rate > 0 else 0
    return 1 + max_iter + extra


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state, base_eval, base_label = select_initial_state(ctx)
    ctx.record(base_state, base_eval, base_label)

    payload = ctx.payload
    mutation_cfg = _mutation_config(payload)
    max_iter = int(payload.get("max_iter", 5))
    population_size = max(1, int(payload.get("population_size", 6)))
    tabu_iter = max(0, int(payload.get("tabu_iter", 3)))
    tabu_rate = min(1.0, max(0.0, float(payload.get("tabu_rate", 0.3))))
    time_shift_hours = float(mutation_cfg.get("time_shift_hours", 0.0))

    seed = int(mutation_cfg.get("mutation_seed") or 0)
    rng = random.Random(seed + 911) if seed else random.Random()

    cur_best_state = base_state
    cur_best_eval = base_eval

    for gen in range(1, max_iter + 1):
        candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for idx in range(population_size):
            iter_no = gen * population_size + idx
            delta = time_shift_hours if ((gen + idx) % 2 == 1) else -time_shift_hours
            mutated = _mutate_state(
                cur_best_state,
                delta_hours=delta,
                iter_no=iter_no,
                mutation_cfg=mutation_cfg,
                problem=ctx.problem,
            )
            m_eval = ctx.evaluate(mutated)
            candidates.append((mutated, m_eval))

        gen_best_state, gen_best_eval = min(candidates, key=lambda item: _rank_key(item[1]))
        ctx.record(gen_best_state, gen_best_eval, f"ga-inline-{gen}")
        if is_better(gen_best_eval, cur_best_eval):
            cur_best_state, cur_best_eval = gen_best_state, gen_best_eval

        if tabu_iter > 0 and tabu_rate > 0 and rng.random() < tabu_rate:
            improved_state, improved_eval = _tabu_search(
                ctx,
                start_state=cur_best_state,
                start_eval=cur_best_eval,
                record_base=False,
                base_label="ga-inline",
                max_iter_override=tabu_iter,
                label_prefix=f"tabu-inline-{gen}",
            )
            if is_better(improved_eval, cur_best_eval):
                cur_best_state, cur_best_eval = improved_state, improved_eval

    return cur_best_state, cur_best_eval


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="ga_tabu_inline",
        name="GA+Tabu (Inline)",
        desc="Her GA jenerasyonunda en iyi aday üzerinde kısa tabu iyileştirme uygulanır.",
        params={
            "max_iter": {"type": "int", "default": 50, "min": 1, "max": 200},
            "population_size": {"type": "int", "default": 20, "min": 1, "max": 50},
            "tabu_iter": {"type": "int", "default": 30, "min": 0, "max": 50},
            "tabu_rate": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0},
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


__all__ = ["ALGO"]
