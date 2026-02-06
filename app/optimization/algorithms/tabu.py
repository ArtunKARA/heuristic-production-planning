from __future__ import annotations

from typing import Any, Dict, Tuple

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .greedy import build_plan as build_greedy_plan


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
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if record_base:
        ctx.record(start_state, start_eval, "greedy")

    molds = ctx.problem.get("resources", {}).get("molds", [])
    alt_mold = None
    if len(molds) > 1:
        alt_mold = molds[1].get("code")
    if not alt_mold:
        return start_state, start_eval

    cur_best_state = start_state
    cur_best_eval = start_eval
    max_iter = int(ctx.payload.get("max_iter", 5))
    for step_idx in range(1, max_iter + 1):
        mutated = _mutate_mold(cur_best_state, alt_mold)
        m_eval = ctx.evaluate(mutated)
        ctx.record(mutated, m_eval, f"tabu-{step_idx}")
        if (m_eval.get("feasible", False) and not cur_best_eval.get("feasible", False)) or (
            m_eval.get("feasible", False) == cur_best_eval.get("feasible", False)
            and float(m_eval.get("total_cost", 1e18)) < float(cur_best_eval.get("total_cost", 1e18))
        ):
            cur_best_state, cur_best_eval = mutated, m_eval

    return cur_best_state, cur_best_eval


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state = build_greedy_plan(ctx.problem)
    base_eval = ctx.evaluate(base_state)
    return _tabu_search(ctx, start_state=base_state, start_eval=base_eval, record_base=True)


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="tabu",
        name="Tabu",
        params={"max_iter": {"type": "int", "default": 5, "min": 1, "max": 200}},
    ),
    planned_iterations=planned_iterations,
    run=run,
)


__all__ = ["ALGO", "_tabu_search"]
