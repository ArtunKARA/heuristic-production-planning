from __future__ import annotations

from typing import Any, Dict

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .ga import (
    _ga_config,
    _ga_create_rng,
    _ga_init_population,
    _ga_run_generation,
    _inject_candidate,
    _mutation_config,
)
from .tabu import _tabu_search
from .utils import is_better, select_initial_state


def planned_iterations(payload: Dict[str, Any]) -> int:
    max_iter = int(payload.get("max_iter", 5))
    tabu_iter = max(0, int(payload.get("tabu_iter", 0)))
    return 1 + max_iter + (max_iter if tabu_iter > 0 else 0)


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state, base_eval, base_label = select_initial_state(ctx)
    ctx.record(base_state, base_eval, base_label)

    payload = ctx.payload
    mutation_cfg = _mutation_config(payload)
    ga_cfg = _ga_config(payload)
    max_iter = int(ga_cfg["max_iter"])
    population_size = int(ga_cfg["population_size"])
    patience = int(ga_cfg.get("early_stop_patience", 0))
    tabu_iter = max(0, int(payload.get("tabu_iter", 3)))
    # Deprecated in this strategy: kept for API compatibility, intentionally ignored.
    _ = payload.get("tabu_rate")

    rng = _ga_create_rng(mutation_cfg, salt=911)
    population = _ga_init_population(
        ctx,
        start_state=base_state,
        start_eval=base_eval,
        mutation_cfg=mutation_cfg,
        ga_cfg=ga_cfg,
    )
    cur_best_state, cur_best_eval = population[0]
    stagnation = 0

    for generation_no in range(1, max_iter + 1):
        improved = False
        population, generation_best = _ga_run_generation(
            ctx,
            population=population,
            generation_no=generation_no,
            mutation_cfg=mutation_cfg,
            ga_cfg=ga_cfg,
            rng=rng,
            label_prefix="ga-inline",
            record_generation=True,
        )
        gen_state, gen_eval = generation_best
        if is_better(gen_eval, cur_best_eval):
            cur_best_state, cur_best_eval = gen_state, gen_eval
            improved = True

        if tabu_iter > 0:
            improved_state, improved_eval = _tabu_search(
                ctx,
                start_state=gen_state,
                start_eval=gen_eval,
                record_base=False,
                max_iter_override=tabu_iter,
                label_prefix="tabu-inline",
                record_step_labels=False,
                summary_label=f"tabu-inline-{generation_no}",
            )
            if is_better(improved_eval, gen_eval):
                population = _inject_candidate(population, (improved_state, improved_eval), population_size)
            if is_better(improved_eval, cur_best_eval):
                cur_best_state, cur_best_eval = improved_state, improved_eval
                improved = True

        pop_best_state, pop_best_eval = population[0]
        if is_better(pop_best_eval, cur_best_eval):
            cur_best_state, cur_best_eval = pop_best_state, pop_best_eval
            improved = True

        if improved:
            stagnation = 0
        else:
            stagnation += 1
        if patience > 0 and stagnation >= patience:
            break

    return cur_best_state, cur_best_eval


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="ga_tabu_inline",
        name="GA+Tabu (Inline)",
        desc="Her GA jenerasyonunda elit aday üzerinde kisa tabu iyilestirme uygulanir.",
        params={
            "max_iter": {"type": "int", "default": 50, "min": 1, "max": 200},
            "population_size": {"type": "int", "default": 20, "min": 2, "max": 100},
            "crossover_rate": {"type": "float", "default": 0.8, "min": 0.0, "max": 1.0},
            "selection_tournament_k": {"type": "int", "default": 3, "min": 2, "max": 10},
            "early_stop_patience": {"type": "int", "default": 0, "min": 0, "max": 200},
            "tabu_iter": {"type": "int", "default": 30, "min": 0, "max": 50},
            "tabu_rate": {
                "type": "float",
                "default": 0.5,
                "min": 0.0,
                "max": 1.0,
                "desc": "Deprecated: GA+Tabu Inline icin artik kullanilmiyor.",
            },
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
