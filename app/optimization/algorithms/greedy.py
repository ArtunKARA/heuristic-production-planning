from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Dict, List

from .base import Algorithm, AlgorithmContext, AlgorithmSpec


def _parse_hhmm(value: str | None) -> time | None:
    if not value or not isinstance(value, str) or ":" not in value:
        return None
    hh, mm = value.split(":", 1)
    try:
        return time(int(hh), int(mm))
    except ValueError:
        return None


def _positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _quantize_qty_to_cavity(qty: float, cavity_step: int) -> float:
    step = max(1, _positive_int(cavity_step, 1))
    value = float(qty)
    if value <= 0.0:
        return 0.0
    quantized = step * int(value // step)
    if quantized <= 0:
        quantized = step
    return float(quantized)


def _bucket_start_time_for_date(problem: Dict[str, Any], day_start) -> time:
    """
    Return earliest shift segment start for the date from work_calendar.
    Fallback to 00:00 when calendar/template data is missing.
    """
    shift_templates = {st.get("code"): st for st in problem.get("shift_templates", [])}
    day_key = day_start
    if isinstance(day_key, str):
        day_key = datetime.fromisoformat(day_key).date()

    def _schedule_day(entry: Dict[str, Any]):
        value = entry.get("date")
        if isinstance(value, str):
            return datetime.fromisoformat(value).date()
        return value

    day_schedule = next((d for d in problem.get("work_calendar", []) if _schedule_day(d) == day_key), None)
    if not day_schedule:
        return datetime.min.time()
    if day_schedule.get("holiday", False):
        return datetime.min.time()

    tpl_code = day_schedule.get("shift_templates_code")
    tpl = shift_templates.get(tpl_code)
    if not tpl:
        return datetime.min.time()

    starts: List[time] = []
    for seg in tpl.get("segments", []):
        start_t = _parse_hhmm(seg.get("start"))
        if start_t is not None:
            starts.append(start_t)
    return min(starts) if starts else datetime.min.time()


def build_plan(problem: Dict[str, Any]) -> Dict[str, Any]:
    """
    Very simple greedy builder:
      - For each order (by due_date), assign to first compatible machine/mold.
      - Respect machine capacity_by_bucket hours; split lots if needed.
      - Start times are placed sequentially from the day's first shift segment.
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
            cavity_step = _positive_int(mold.get("eye"), 1)

            step = process_steps.get((pcode, "AP300"))
            if not step:
                continue
            base_qty = float(step.get("base_qty", 1))
            cycle_sec = float(step.get("cycle_time_sec", 1))
            setup_min = float(step.get("setup_time_min", 0))
            per_unit_sec = cycle_sec / base_qty if base_qty else cycle_sec

            cap_hours = float(machine.get("capacity_by_bucket", {}).get(bucket_id, 0.0))
            used_hours = 0.0
            start_time = datetime.combine(day_start, _bucket_start_time_for_date(problem, day_start))

            while remaining > 0 and used_hours < cap_hours:
                # available hours left
                avail_h = cap_hours - used_hours
                # max units by hours
                lot_qty = min(remaining, (avail_h * 3600 - setup_min * 60) / per_unit_sec if per_unit_sec else remaining)
                lot_qty = _quantize_qty_to_cavity(lot_qty, cavity_step)
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
