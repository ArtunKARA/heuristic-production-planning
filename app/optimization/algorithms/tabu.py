from __future__ import annotations

from typing import Any, Dict, Tuple

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .ga import _mutate_state, _mutation_config
from .utils import is_better, select_initial_state


def _mutate_mold(state: Dict[str, Any], alt_mold: str) -> Dict[str, Any]:
    mutated = {"lots": []}
    for lot in state.get("lots", []):
        lot_copy = dict(lot)
        ar = dict(lot_copy.get("assigned_resources", {}))
        if "mold" in ar:
            ar["mold"] = alt_mold
        lot_copy["assigned_resources"] = ar
        mutated["lots"].append(lot_copy)
    return mutated


def planned_iterations(payload: Dict[str, Any]) -> int:
    max_iter = int(payload.get("max_iter", 5))
    return 1 + max_iter


def _tabu_search(
    ctx: AlgorithmContext,
    *,
    start_state: Dict[str, Any],
    start_eval: Dict[str, Any],
    record_base: bool,
    base_label: str = "greedy",
    max_iter_override: int | None = None,
    label_prefix: str = "tabu",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if record_base:
        ctx.record(start_state, start_eval, base_label)

    molds = ctx.problem.get("resources", {}).get("molds", [])
    alt_mold = None
    if len(molds) > 1:
        alt_mold = molds[1].get("code")

    cur_best_state = start_state
    cur_best_eval = start_eval
    max_iter = int(max_iter_override if max_iter_override is not None else ctx.payload.get("max_iter", 5))
    mutation_cfg = _mutation_config(ctx.payload)
    if "time_shift_hours" not in ctx.payload:
        mutation_cfg["time_shift_hours"] = 0.0
    time_shift_hours = float(mutation_cfg.get("time_shift_hours") or 0.0)
    for step_idx in range(1, max_iter + 1):
        mutated = _mutate_mold(cur_best_state, alt_mold) if alt_mold else cur_best_state
        delta_hours = 0.0
        if time_shift_hours:
            delta_hours = time_shift_hours if (step_idx % 2 == 1) else -time_shift_hours
        mutated = _mutate_state(
            mutated,
            delta_hours=delta_hours,
            iter_no=step_idx,
            mutation_cfg=mutation_cfg,
            problem=ctx.problem,
        )
        m_eval = ctx.evaluate(mutated)
        ctx.record(mutated, m_eval, f"{label_prefix}-{step_idx}")
        if is_better(m_eval, cur_best_eval):
            cur_best_state, cur_best_eval = mutated, m_eval

    return cur_best_state, cur_best_eval


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state, base_eval, base_label = select_initial_state(ctx)
    return _tabu_search(ctx, start_state=base_state, start_eval=base_eval, record_base=True, base_label=base_label)


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="tabu",
        name="Tabu",
        params={
            "max_iter": {"type": "int", "default": 50, "min": 1, "max": 200},
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


__all__ = ["ALGO", "_tabu_search"]