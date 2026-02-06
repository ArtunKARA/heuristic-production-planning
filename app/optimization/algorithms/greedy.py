from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from .base import Algorithm, AlgorithmContext, AlgorithmSpec


def build_plan(problem: Dict[str, Any]) -> Dict[str, Any]:
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
            if not bucket_id or bucket_id not in time_buckets:
                continue
            tb = time_buckets[bucket_id]
            day_start = tb["start_date"]
            if isinstance(day_start, str):
                day_start = datetime.fromisoformat(day_start).date()

            if not machines or not molds:
                continue

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


def planned_iterations(payload: Dict[str, Any]) -> int:
    return 1


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = build_plan(ctx.problem)
    eval_res = ctx.evaluate(state)
    ctx.record(state, eval_res, "greedy")
    return state, eval_res


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="greedy",
        name="Greedy",
        params={"max_iter": {"type": "int", "default": 1, "min": 1, "max": 1}},
    ),
    planned_iterations=planned_iterations,
    run=run,
)
