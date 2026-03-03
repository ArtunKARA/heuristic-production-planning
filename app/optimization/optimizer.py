# TR: Optimizasyon katmani icin plug-in girisi (moduler).
# EN: Optimization layer plug-in entry (modular).
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Any, Callable

from app.evaluation.evaluate_state import evaluate_state
from app.frame.models.problem import ProblemFrame
from app.frame.ingest.normalizer import normalize_problem_frame
from app.optimization.algorithms import AlgorithmContext, get_algorithm, list_algorithm_specs
from app.optimization.algorithms.greedy import build_plan as build_greedy_plan


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _setup_minutes_by_product_process(problem: Dict[str, Any]) -> Dict[tuple[str, str], float]:
    out: Dict[tuple[str, str], float] = {}
    for product in problem.get("products", []):
        pcode = product.get("code")
        if not pcode:
            continue
        for step in product.get("process_data", []):
            proc = step.get("process_code")
            if not proc:
                continue
            out[(pcode, proc)] = float(step.get("setup_time_min", 0.0) or 0.0)
    return out


def _ensure_setup_times(state_dict: Dict[str, Any], problem: Dict[str, Any]) -> None:
    if not isinstance(state_dict, dict):
        return
    lots = state_dict.get("lots")
    if not isinstance(lots, list) or not lots:
        return

    setup_by_step = _setup_minutes_by_product_process(problem)
    for lot in lots:
        if not isinstance(lot, dict):
            continue

        process_start_dt = _as_datetime(lot.get("process_start_time"))
        if process_start_dt is None:
            continue

        setup_start_dt = _as_datetime(lot.get("setup_start_time")) or process_start_dt
        if not lot.get("setup_start_time"):
            lot["setup_start_time"] = setup_start_dt.isoformat()

        if lot.get("setup_end_time"):
            continue

        setup_min = max(
            0.0,
            setup_by_step.get((lot.get("product_code"), lot.get("process_code")), 0.0),
        )
        setup_end_dt = setup_start_dt + timedelta(minutes=setup_min)

        process_end_dt = _as_datetime(lot.get("process_end_time"))
        if process_end_dt is not None and setup_end_dt > process_end_dt:
            setup_end_dt = process_end_dt
        lot["setup_end_time"] = setup_end_dt.isoformat()


def list_algorithms(problem: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    molds = (problem or {}).get("resources", {}).get("molds", []) if problem else []
    has_molds = bool(molds and len(molds) > 1)
    algos = list_algorithm_specs()
    if not has_molds:
        allowed = {"greedy", "ga", "tabu", "gatabu", "ga_tabu_inline", "ga_tabu_topk", "hho", "hmpa", "cssrank"}
        algos = [a for a in algos if a["code"] in allowed]
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
      - strategy: "ga_tabu_inline"
      - strategy: "ga_tabu_topk"
      - strategy: "hho"
      - strategy: "hmpa"
      - strategy: "cssrank"
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
    frame_state = norm.get("state") or {}

    strategy = (payload.get("strategy") or "greedy").lower()
    algo = get_algorithm(strategy) or get_algorithm("greedy")
    if algo is None:
        raise ValueError(f"Unknown strategy: {strategy}")

    def hard_total(res: Dict[str, Any]) -> float:
        cr = res.get("constraint_results", {})
        return sum(float(v.get("violation", 0.0)) for code, v in cr.items() if code.startswith("HARD_"))

    def evaluate_state_dict(state_dict: Dict[str, Any]):
        _ensure_setup_times(state_dict, problem)
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
                "eval_calls_total": int(ctx.eval_calls_total),
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
        frame_state=frame_state,
        evaluate=evaluate_state_dict,
        record=record,
        build_greedy_plan=build_greedy_plan,
    )

    best_state, best_eval = algo.run(ctx)
    _ensure_setup_times(best_state, problem)
    best_hard_total = float(hard_total(best_eval))
    feasible_best = bool(best_eval.get("feasible", best_hard_total == 0.0))
    if best_hard_total == 0.0:
        feasible_best = True

    return {
        "best_index": 0,
        "best_state": best_state,
        "best_evaluation": best_eval,
        "best_hard_total": best_hard_total,
        "feasible_best": feasible_best,
        "eval_calls_total": int(ctx.eval_calls_total),
        "iterations": iterations,
    }
