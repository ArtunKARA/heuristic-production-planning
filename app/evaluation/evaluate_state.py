"""evaluate_state entrypoint

Implements the external API described in the draw.io/pseudocode:
    evaluate_state(state, problemData, scenarioConfig) -> EvaluationResult

The heavy lifting (context build + constraint evaluation) lives in
``app.evaluation.eval_core`` which is the patched pseudocode from Doc/build_resource_layer.py.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Tuple

from app.evaluation import eval_core
from app.frame.ingest.normalizer import normalize_problem_frame


def _frame_to_canonical(problem_data: Dict[str, Any], state: Dict[str, Any], scenario_config: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Take raw/canonicalized dict parts and produce the dictionaries that
    eval_core.build_evaluation_context expects: keys ``problem``, ``state``, ``scenario``.
    ``normalize_problem_frame`` already mutates keys like machines/molds, inputs, etc.
    """

    # normalize again defensively (idempotent)
    frame = normalize_problem_frame({
        "problemData": deepcopy(problem_data),
        "state": deepcopy(state),
        "scenarioConfig": deepcopy(scenario_config),
    })

    pd = frame["problemData"]
    st = frame["state"]
    sc = frame["scenario"]

    # eval_core expects ``problem`` with resource keys machines/molds, etc.
    problem = pd
    scenario = sc

    # datetime objects must exist for start/end; ensure parse if still strings
    def _ensure_dt(val: Any) -> Any:
        if isinstance(val, str):
            return datetime.fromisoformat(val)
        return val

    for lot in st.get("lots", []):
        for key in ("setup_start_time", "setup_end_time", "process_start_time", "process_end_time"):
            if lot.get(key):
                lot[key] = _ensure_dt(lot[key])

    return problem, st, scenario


def evaluate_state(state: Dict[str, Any], problemData: Dict[str, Any], scenarioConfig: Dict[str, Any]) -> Dict[str, Any]:
    """External evaluation entrypoint.

    Returns a dictionary with keys:
      - feasible: bool
      - total_cost: float
      - constraint_results: {code: {violation, penalty}}
      - kpi_results: {code: value}
    """

    problem, st, scenario = _frame_to_canonical(problemData, state, scenarioConfig)

    ctx = eval_core.build_evaluation_context(problem, st, scenario)
    res = eval_core.evaluate_constraints(ctx)

    constraint_results: Dict[str, Dict[str, float]] = {}
    # hard items -> violation, no penalty (only feasibility)
    for item in res["hard"]["items"]:
        constraint_results[item["code"]] = {
            "violation": float(item.get("value", 0.0)),
            "penalty": 0.0,
        }

    # soft terms -> penalty = cost, violation = value
    for term in res["soft"]["terms"]:
        constraint_results[term["code"]] = {
            "violation": float(term.get("value", 0.0)),
            "penalty": float(term.get("cost", 0.0)),
        }

    kpi_results: Dict[str, float] = {t["code"]: float(t.get("value", 0.0)) for t in res["soft"].get("terms", [])}

    return {
        "feasible": bool(res.get("feasible", False)),
        "total_cost": float(res["soft"].get("total_cost", 0.0)),
        "constraint_results": constraint_results,
        "kpi_results": kpi_results,
    }


__all__ = ["evaluate_state"]
