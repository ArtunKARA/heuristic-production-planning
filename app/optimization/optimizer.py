# TR: Optimizasyon katmani icin plug-in girisi (stub).
# EN: Optimization layer plug-in entry (stub).
from __future__ import annotations

from typing import Dict, List, Any, Callable
from datetime import datetime, timedelta

from app.evaluation.evaluate_state import evaluate_state
from app.frame.models.problem import ProblemFrame
from app.frame.ingest.normalizer import normalize_problem_frame


def _greedy_plan(problem: Dict[str, Any]) -> Dict[str, Any]:
    """
    Very simple greedy builder:
      - For each order (by due_date), assign to first compatible machine/mold.
      - Respect machine capacity_by_bucket hours; split lots if needed.
      - Start times are placed sequentially within the bucket day at 00:00.
    """
    time_buckets = {tb["id"]: tb for tb in problem.get("time_buckets", [])}
    machines = problem.get("resources", {}).get("machines", [])
    molds = problem.get("resources", {}).get("molds", [])
    compat_pairs = {
        (p["machine_id"], p["mold_code"], p.get("process_code")): True
        for p in problem.get("compatibility", {}).get("machine_mold_pairs", [])
    }

    products = {p["code"]: p for p in problem.get("products", [])}
    process_steps = {}
    for p in products.values():
        for step in p.get("process_data", []):
            process_steps[(p["code"], step["process_code"])] = step

    lots: List[Dict[str, Any]] = []
    ONE_HOUR = timedelta(hours=1)

    for og in problem.get("orders", []):
        pcode = og["product_code"]
        for order in og.get("orders", []):
            remaining = float(order["qty"])
            bucket_id = order.get("time_bucket_id")
            if not bucket_id:
                continue
            tb = time_buckets[bucket_id]
            day_start = tb["start_date"]
            if isinstance(day_start, str):
                day_start = datetime.fromisoformat(day_start).date()

            # pick machine/mold
            machine = next((m for m in machines if m.get("process_code") == "AP300"), machines[0])
            mold = next((m for m in molds if m.get("process_code") == "AP300"), molds[0])
            if not compat_pairs.get((machine["id"], mold["code"], "AP300"), True):
                mold = molds[0]

            step = process_steps.get((pcode, "AP300"))
            if not step:
                continue
            base_qty = float(step.get("base_qty", 1))
            cycle_sec = float(step.get("cycle_time_sec", 1))
            setup_min = float(step.get("setup_time_min", 0))
            per_unit_sec = cycle_sec / base_qty if base_qty else cycle_sec

            cap_hours = float(machine.get("capacity_by_bucket", {}).get(bucket_id, 0.0))
            used_hours = 0.0
            start_time = datetime.combine(day_start, datetime.min.time())

            while remaining > 0 and used_hours < cap_hours:
                # available hours left
                avail_h = cap_hours - used_hours
                # max units by hours
                lot_qty = min(remaining, (avail_h * 3600 - setup_min * 60) / per_unit_sec if per_unit_sec else remaining)
                if lot_qty <= 0:
                    break

                process_seconds = lot_qty * per_unit_sec + setup_min * 60
                dur = timedelta(seconds=process_seconds)
                lot_start = start_time + timedelta(hours=used_hours)
                lot_end = lot_start + dur

                lots.append({
                    "lot_id": f"{order.get('order_id','O')}_{len(lots)+1}",
                    "product_code": pcode,
                    "process_code": "AP300",
                    "time_bucket_id": bucket_id,
                    "qty": lot_qty,
                    "process_start_time": lot_start.isoformat(),
                    "process_end_time": lot_end.isoformat(),
                    "assigned_resources": {"machine": machine["id"], "mold": mold["code"]},
                })

                remaining -= lot_qty
                used_hours += process_seconds / 3600.0

    return {"lots": lots}


def _shift_time(dt_str: str, delta_hours: float) -> str:
    dt = datetime.fromisoformat(dt_str)
    return (dt + timedelta(hours=delta_hours)).isoformat()


def _mutate_time(state: Dict[str, Any], delta_hours: float) -> Dict[str, Any]:
    mutated = {"lots": []}
    for lot in state.get("lots", []):
        lot_copy = dict(lot)
        for key in ("process_start_time", "process_end_time", "setup_start_time", "setup_end_time"):
            if key in lot_copy and lot_copy[key]:
                lot_copy[key] = _shift_time(lot_copy[key], delta_hours)
        mutated["lots"].append(lot_copy)
    return mutated


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


def list_algorithms(problem: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    molds = (problem or {}).get("resources", {}).get("molds", []) if problem else []
    has_molds = bool(molds and len(molds) > 1)
    algos = [
        {
            "code": "greedy",
            "name": "Greedy",
            "params": {"max_iter": {"type": "int", "default": 1, "min": 1, "max": 1}},
        },
        {
            "code": "ga",
            "name": "GA",
            "params": {"max_iter": {"type": "int", "default": 5, "min": 1, "max": 200}},
        },
        {
            "code": "tabu",
            "name": "Tabu",
            "params": {"max_iter": {"type": "int", "default": 5, "min": 1, "max": 200}},
        },
        {
            "code": "gatabu",
            "name": "GA+Tabu",
            "params": {"max_iter": {"type": "int", "default": 5, "min": 1, "max": 200}},
        },
    ]
    if not has_molds:
        algos = [a for a in algos if a["code"] in ("greedy", "ga")]
    return algos


def optimize_frame(frame: ProblemFrame, payload: Dict[str, object], event_sink: Callable[[Dict[str, Any]], None] | None = None) -> Dict[str, object]:
    """
    Simple strategy-based optimizer (placeholder for heuristics GA/Tabu etc.):
      - strategy: "greedy" (default) builds a plan from problemData.
      - strategy: "ga" runs a lightweight evolutionary loop (mutations on time).
      - strategy: "tabu" runs mold-switch iterations to reduce night changes/changeovers.
      - strategy: "gatabu" runs ga then a tabu refinement on best.
      - evaluate_state used for scoring.
      - Returns best plan + iteration log (iteration_no, feasible, cost, hard_total).

    Returns:
      {
        "best_index": int,
        "best_state": {...},
        "best_evaluation": {...},
        "iterations": [
            {"iteration_no": k, "feasible": bool, "total_cost": float, "hard_total": float, "evaluation": {...}}
        ]
      }
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

    def build_candidate() -> Dict[str, Any]:
        if strategy == "greedy":
            return _greedy_plan(problem)
        return _greedy_plan(problem)

    def hard_total(res: Dict[str, Any]) -> float:
        cr = res.get("constraint_results", {})
        return sum(v.get("violation", 0.0) for code, v in cr.items() if code.startswith("HARD_"))

    def evaluate_state_dict(state_dict: Dict[str, Any]):
        return evaluate_state(state=state_dict, problemData=problem, scenarioConfig=scenario)

    max_iter = int(payload.get("max_iter", 5))
    iterations: List[Dict[str, Any]] = []

    def record(iter_no: int, state_dict: Dict[str, Any], eval_res: Dict[str, Any], label: str, total_planned: int):
        iterations.append(
            {
                "type": "iteration",
                "iteration_no": iter_no,
                "feasible": bool(eval_res.get("feasible", False)),
                "total_cost": float(eval_res.get("total_cost", 0.0)),
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

    # plan total iteration count for progress
    planned_ga = max_iter if strategy in ("ga", "gatabu") else 0
    planned_tabu = max_iter if strategy in ("tabu", "gatabu") else 0
    total_planned = 1 + planned_ga + planned_tabu

    # --- Greedy base ---
    base_state = build_candidate()
    best_state = base_state
    best_eval = evaluate_state_dict(best_state)
    record(1, best_state, best_eval, "greedy", total_planned)

    # --- GA strategy ---
    if strategy in ("ga", "gatabu"):
        cur_best_state = best_state
        cur_best_eval = best_eval
        for i in range(2, max_iter + 2):
            # simple mutation: shift times by +2h or -2h alternating
            delta = 2.0 if i % 2 == 0 else -2.0
            mutated = _mutate_time(cur_best_state, delta)
            m_eval = evaluate_state_dict(mutated)
            record(i, mutated, m_eval, f"ga-{i-1}", total_planned)
            if (m_eval.get("feasible", False) and not cur_best_eval.get("feasible", False)) or (
                m_eval.get("feasible", False) == cur_best_eval.get("feasible", False)
                and float(m_eval.get("total_cost", 1e18)) < float(cur_best_eval.get("total_cost", 1e18))
            ):
                cur_best_state, cur_best_eval = mutated, m_eval
        best_state, best_eval = cur_best_state, cur_best_eval

    # --- TABU-like strategy (mold swap tries) ---
    if strategy in ("tabu", "gatabu"):
        # find alternate mold if exists
        molds = problem.get("resources", {}).get("molds", [])
        alt_mold = None
        if len(molds) > 1:
            alt_mold = molds[1].get("code")
        if alt_mold:
            start_iter = len(iterations) + 1
            cur_best_state = best_state
            cur_best_eval = best_eval
            for j in range(start_iter, start_iter + max_iter):
                mutated = _mutate_mold(cur_best_state, alt_mold)
                m_eval = evaluate_state_dict(mutated)
                record(j, mutated, m_eval, f"tabu-{j - start_iter + 1}", total_planned)
                if (m_eval.get("feasible", False) and not cur_best_eval.get("feasible", False)) or (
                    m_eval.get("feasible", False) == cur_best_eval.get("feasible", False)
                    and float(m_eval.get("total_cost", 1e18)) < float(cur_best_eval.get("total_cost", 1e18))
                ):
                    cur_best_state, cur_best_eval = mutated, m_eval
            best_state, best_eval = cur_best_state, cur_best_eval

    return {
        "best_index": 0,
        "best_state": best_state,
        "best_evaluation": best_eval,
        "iterations": iterations,
    }
