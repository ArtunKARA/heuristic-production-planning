# TR: Optimizasyon katmani icin plug-in girisi (moduler).
# EN: Optimization layer plug-in entry (modular).
from __future__ import annotations

from typing import Dict, List, Any, Callable

from app.evaluation.evaluate_state import evaluate_state
from app.frame.models.problem import ProblemFrame
from app.frame.ingest.normalizer import normalize_problem_frame
from app.optimization.algorithms import AlgorithmContext, get_algorithm, list_algorithm_specs
from app.optimization.algorithms.greedy import build_plan as build_greedy_plan


def list_algorithms(problem: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    molds = (problem or {}).get("resources", {}).get("molds", []) if problem else []
    has_molds = bool(molds and len(molds) > 1)
    algos = list_algorithm_specs()
    if not has_molds:
        algos = [a for a in algos if a["code"] in ("greedy", "ga")]
    return algos


def optimize_frame(
    frame: ProblemFrame,
    payload: Dict[str, object],
    event_sink: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, object]:
    """
    Strategy-based optimizer (modular):
      - strategy: "greedy" (default)
      - strategy: "ga"
      - strategy: "tabu"
      - strategy: "gatabu"
      - evaluate_state used for scoring.
      - Returns best plan + iteration log (iteration_no, feasible, cost, hard_total).
    """
    raw = {
        "problemData": frame.problemData.model_dump(mode="json", by_alias=True),
        "state": frame.state.model_dump(mode="json", by_alias=True),
        "scenarioConfig": frame.scenarioConfig.model_dump(mode="json", by_alias=True),
    }
    norm = normalize_problem_frame(raw)
    problem = norm["problemData"]
    scenario = norm["scenarioConfig"]

    strategy = (payload.get("strategy") or "greedy").lower()
    algo = get_algorithm(strategy) or get_algorithm("greedy")
    if algo is None:
        raise ValueError(f"Unknown strategy: {strategy}")

    def hard_total(res: Dict[str, Any]) -> float:
        cr = res.get("constraint_results", {})
        return sum(float(v.get("violation", 0.0)) for code, v in cr.items() if code.startswith("HARD_"))

    def evaluate_state_dict(state_dict: Dict[str, Any]):
        return evaluate_state(state=state_dict, problemData=problem, scenarioConfig=scenario)

    iterations: List[Dict[str, Any]] = []
    total_planned = max(1, int(algo.planned_iterations(payload)))
    iter_no = 0

    def record(state_dict: Dict[str, Any], eval_res: Dict[str, Any], label: str):
        nonlocal iter_no
        iter_no += 1
        iterations.append(
            {
                "type": "iteration",
                "iteration_no": iter_no,
                "feasible": bool(eval_res.get("feasible", False)),
                "total_cost": float(eval_res.get("total_cost", 0.0)),
                "total_score": float(eval_res.get("total_score", eval_res.get("total_cost", 0.0))),
                "hard_total": float(hard_total(eval_res)),
                "evaluation": eval_res,
                "state": state_dict,
                "progress": {
                    "label": label,
                    "of": total_planned,
                    "remaining": max(total_planned - iter_no, 0),
                    "pct": round(100.0 * iter_no / total_planned, 2),
                },
            }
        )
        if event_sink:
            event_sink(iterations[-1])

    ctx = AlgorithmContext(
        problem=problem,
        scenario=scenario,
        payload=payload,
        evaluate=evaluate_state_dict,
        record=record,
        build_greedy_plan=build_greedy_plan,
    )

    best_state, best_eval = algo.run(ctx)

    return {
        "best_index": 0,
        "best_state": best_state,
        "best_evaluation": best_eval,
        "iterations": iterations,
    }
