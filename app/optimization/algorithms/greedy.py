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


def _day_value(value: Any):
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _calendar_by_date(problem: Dict[str, Any]) -> Dict[Any, Dict[str, Any]]:
    out: Dict[Any, Dict[str, Any]] = {}
    for entry in problem.get("work_calendar", []):
        d = _day_value(entry.get("date"))
        if d is not None and d not in out:
            out[d] = entry
    return out


def _segments_for_day(
    day_start,
    *,
    shift_templates: Dict[str, Dict[str, Any]],
    day_schedule_by_date: Dict[Any, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    day = _day_value(day_start)
    day_schedule = day_schedule_by_date.get(day)
    if not day_schedule or day_schedule.get("holiday", False):
        return []

    tpl = shift_templates.get(day_schedule.get("shift_templates_code"))
    if not tpl:
        return []

    segments: List[Dict[str, Any]] = []
    for seg in tpl.get("segments", []):
        start_t = _parse_hhmm(seg.get("start"))
        end_t = _parse_hhmm(seg.get("end"))
        if start_t is None or end_t is None:
            continue
        start_dt = datetime.combine(day, start_t)
        end_dt = datetime.combine(day, end_t)
        if seg.get("end") == "00:00" or end_dt <= start_dt:
            end_dt += timedelta(days=1)
        segments.append({
            "start": start_dt,
            "end": end_dt,
            "constraints": set(seg.get("constraints", []) or []),
        })
    return segments


def _segment_for_datetime(
    current: datetime,
    *,
    shift_templates: Dict[str, Dict[str, Any]],
    day_schedule_by_date: Dict[Any, Dict[str, Any]],
) -> Dict[str, Any] | None:
    for day in (current.date(), current.date() - timedelta(days=1)):
        for seg in _segments_for_day(day, shift_templates=shift_templates, day_schedule_by_date=day_schedule_by_date):
            if seg["start"] <= current < seg["end"]:
                return seg
    return None


def _align_setup_start(
    current: datetime,
    *,
    shift_templates: Dict[str, Dict[str, Any]],
    day_schedule_by_date: Dict[Any, Dict[str, Any]],
    forbidden_constraints: set[str],
) -> datetime:
    if not forbidden_constraints:
        return current

    probe = current
    for _ in range(64):
        seg = _segment_for_datetime(probe, shift_templates=shift_templates, day_schedule_by_date=day_schedule_by_date)
        if seg is not None:
            if not (seg["constraints"] & forbidden_constraints):
                return probe
            probe = seg["end"]
            continue

        day = probe.date()
        allowed_starts = [
            s["start"]
            for s in _segments_for_day(day, shift_templates=shift_templates, day_schedule_by_date=day_schedule_by_date)
            if not (s["constraints"] & forbidden_constraints)
        ]
        if allowed_starts:
            next_start = min(allowed_starts)
            if probe <= next_start:
                probe = next_start
            else:
                probe = datetime.combine(day + timedelta(days=1), time.min)
        else:
            probe = datetime.combine(day + timedelta(days=1), time.min)
    return current


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
    product_molds = {
        (pm.get("product_code"), pm.get("process_code")): set(pm.get("allowed_molds", []) or [])
        for pm in problem.get("compatibility", {}).get("product_molds", [])
    }

    products = {p["code"]: p for p in problem.get("products", [])}
    process_steps = {}
    for p in products.values():
        for step in p.get("process_data", []):
            process_steps[(p["code"], step["process_code"])] = step
    shift_templates = {st.get("code"): st for st in problem.get("shift_templates", [])}
    day_schedule_by_date = _calendar_by_date(problem)

    machines_ap300 = [m for m in machines if m.get("process_code") == "AP300"] or machines
    molds_ap300 = [m for m in molds if m.get("process_code") == "AP300"] or molds
    if not machines_ap300 or not molds_ap300:
        return {"lots": []}

    order_items: List[tuple[datetime, str, Dict[str, Any]]] = []
    for og in problem.get("orders", []):
        pcode = og.get("product_code")
        for order in og.get("orders", []):
            due_raw = order.get("due_date")
            try:
                due_dt = datetime.fromisoformat(due_raw) if isinstance(due_raw, str) else datetime.max
            except ValueError:
                due_dt = datetime.max
            order_items.append((due_dt, pcode, order))
    order_items.sort(key=lambda item: (item[0], str(item[2].get("order_id", ""))))

    lots: List[Dict[str, Any]] = []
    machine_usage_by_bucket: Dict[tuple[Any, str], float] = {}
    machine_cursor_by_machine: Dict[Any, datetime] = {}

    for _due_dt, pcode, order in order_items:
        remaining = float(order.get("qty") or 0.0)
        if remaining <= 0.0:
            continue

        bucket_id = order.get("time_bucket_id")
        if not bucket_id or bucket_id not in time_buckets:
            continue
        tb = time_buckets[bucket_id]
        day_start = tb.get("start_date")
        if isinstance(day_start, str):
            day_start = datetime.fromisoformat(day_start).date()
        bucket_floor = datetime.combine(day_start, time.min)

        step = process_steps.get((pcode, "AP300"))
        if not step:
            continue
        base_qty = float(step.get("base_qty", 1) or 1)
        cycle_sec = float(step.get("cycle_time_sec", 1) or 1)
        setup_min = float(step.get("setup_time_min", 0) or 0)
        per_unit_sec = cycle_sec / base_qty if base_qty else cycle_sec
        if per_unit_sec <= 0:
            per_unit_sec = 1.0

        while remaining > 0.0:
            candidates = []
            for machine in machines_ap300:
                machine_id = machine.get("id")
                cap_hours = float(machine.get("capacity_by_bucket", {}).get(bucket_id, 0.0))
                used_hours = float(machine_usage_by_bucket.get((machine_id, bucket_id), 0.0))
                avail_hours = cap_hours - used_hours
                if avail_hours <= 1e-9:
                    continue

                allowed_molds = product_molds.get((pcode, "AP300"))
                mold_candidates = []
                for mold in molds_ap300:
                    mold_code = mold.get("code")
                    if not mold_code:
                        continue
                    if allowed_molds is not None and mold_code not in allowed_molds:
                        continue
                    if compat_pairs:
                        ok = compat_pairs.get((machine_id, mold_code, "AP300"), False) or compat_pairs.get((machine_id, mold_code, None), False)
                        if not ok:
                            continue
                    mold_candidates.append(mold)
                if not mold_candidates:
                    continue

                mold_candidates.sort(key=lambda m: _positive_int(m.get("eye"), 1), reverse=True)
                mold = mold_candidates[0]
                forbidden_setup_constraints = {"NO_MOLD_CHANGE_AT_NIGHT"} if mold.get("code") else set()

                machine_cursor = machine_cursor_by_machine.get(machine_id, bucket_floor)
                if machine_cursor < bucket_floor:
                    machine_cursor = bucket_floor
                aligned_cursor = _align_setup_start(
                    machine_cursor,
                    shift_templates=shift_templates,
                    day_schedule_by_date=day_schedule_by_date,
                    forbidden_constraints=forbidden_setup_constraints,
                )

                candidates.append({
                    "machine": machine,
                    "machine_id": machine_id,
                    "mold": mold,
                    "avail_hours": avail_hours,
                    "cursor": aligned_cursor,
                    "forbidden_setup_constraints": forbidden_setup_constraints,
                })

            if not candidates:
                break

            candidates.sort(key=lambda c: (c["cursor"], -c["avail_hours"], c["machine_id"]))
            chosen = candidates[0]
            machine = chosen["machine"]
            machine_id = chosen["machine_id"]
            mold = chosen["mold"]
            cursor = chosen["cursor"]
            avail_hours = chosen["avail_hours"]
            forbidden_setup_constraints = chosen["forbidden_setup_constraints"]

            cavity_step = _positive_int(mold.get("eye"), 1)
            max_qty_by_time = (avail_hours * 3600.0 - setup_min * 60.0) / per_unit_sec
            lot_qty = min(remaining, max_qty_by_time if max_qty_by_time > 0 else 0.0)
            lot_qty = _quantize_qty_to_cavity(lot_qty, cavity_step)
            if lot_qty <= 0.0:
                machine_usage_by_bucket[(machine_id, bucket_id)] = float(machine.get("capacity_by_bucket", {}).get(bucket_id, 0.0))
                continue

            process_seconds = lot_qty * per_unit_sec + setup_min * 60.0
            max_seconds = avail_hours * 3600.0
            if process_seconds > max_seconds + 1e-6:
                lot_qty = _quantize_qty_to_cavity(max_qty_by_time, cavity_step)
                if lot_qty <= 0.0:
                    machine_usage_by_bucket[(machine_id, bucket_id)] = float(machine.get("capacity_by_bucket", {}).get(bucket_id, 0.0))
                    continue
                process_seconds = lot_qty * per_unit_sec + setup_min * 60.0
                if process_seconds > max_seconds + 1e-6:
                    machine_usage_by_bucket[(machine_id, bucket_id)] = float(machine.get("capacity_by_bucket", {}).get(bucket_id, 0.0))
                    continue

            lot_start = _align_setup_start(
                cursor,
                shift_templates=shift_templates,
                day_schedule_by_date=day_schedule_by_date,
                forbidden_constraints=forbidden_setup_constraints,
            )
            lot_end = lot_start + timedelta(seconds=process_seconds)
            setup_end = lot_start + timedelta(minutes=setup_min)

            lots.append({
                "lot_id": f"{order.get('order_id','O')}_{len(lots)+1}",
                "product_code": pcode,
                "process_code": "AP300",
                "time_bucket_id": bucket_id,
                "qty": lot_qty,
                "setup_start_time": lot_start.isoformat(),
                "setup_end_time": setup_end.isoformat(),
                "process_start_time": lot_start.isoformat(),
                "process_end_time": lot_end.isoformat(),
                "assigned_resources": {"machine": machine_id, "mold": mold.get("code")},
            })

            remaining -= lot_qty
            machine_usage_by_bucket[(machine_id, bucket_id)] = float(machine_usage_by_bucket.get((machine_id, bucket_id), 0.0)) + (process_seconds / 3600.0)
            machine_cursor_by_machine[machine_id] = lot_end

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
