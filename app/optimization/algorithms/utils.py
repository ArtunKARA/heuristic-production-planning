from __future__ import annotations

from typing import Any, Dict, Tuple


def total_score(eval_res: Dict[str, Any]) -> float:
    if eval_res is None:
        return float("inf")
    if "total_score" in eval_res:
        return float(eval_res.get("total_score") or 0.0)
    return float(eval_res.get("total_cost") or 0.0)


def is_better(candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    cand_feasible = bool(candidate.get("feasible", False))
    cur_feasible = bool(current.get("feasible", False))
    if cand_feasible != cur_feasible:
        return cand_feasible and not cur_feasible
    return total_score(candidate) < total_score(current)


def select_initial_state(ctx) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    greedy_state = ctx.build_greedy_plan(ctx.problem)
    greedy_eval = ctx.evaluate(greedy_state)

    state = ctx.frame_state if ctx.frame_state and ctx.frame_state.get("lots") else None
    if not state:
        return greedy_state, greedy_eval, "greedy"

    state_eval = ctx.evaluate(state)
    if is_better(state_eval, greedy_eval):
        return state, state_eval, "state"

    return greedy_state, greedy_eval, "greedy"


__all__ = ["is_better", "select_initial_state", "total_score"]
