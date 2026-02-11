from __future__ import annotations

import random
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Tuple

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .utils import is_better, select_initial_state


def _shift_time(dt_str: str, delta_hours: float) -> str:
    dt = datetime.fromisoformat(dt_str)
    return (dt + timedelta(hours=delta_hours)).isoformat()


def _positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _quantize_qty_to_cavity(qty: Any, cavity_step: int) -> float:
    step = max(1, _positive_int(cavity_step, 1))
    try:
        value = float(qty)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0.0:
        return 0.0
    # keep quantities as cavity-multiples (e.g. eye=100 => 100,200,...)
    quantized = step * int(value // step)
    if quantized <= 0:
        quantized = step
    return float(quantized)


def _build_compat_indexes(problem: Dict[str, Any]) -> Dict[str, Any]:
    machines = problem.get("resources", {}).get("machines", [])
    molds = problem.get("resources", {}).get("molds", [])
    compat_pairs = set(
        (p["machine_id"], p["mold_code"], p.get("process_code"))
        for p in problem.get("compatibility", {}).get("machine_mold_pairs", [])
    )
    product_molds = {}
    for pm in problem.get("compatibility", {}).get("product_molds", []):
        key = (pm.get("product_code"), pm.get("process_code"))
        product_molds[key] = set(pm.get("allowed_molds", []) or [])

    machines_by_process = {}
    for m in machines:
        machines_by_process.setdefault(m.get("process_code"), []).append(m)

    molds_by_process = {}
    mold_eye_by_code = {}
    for m in molds:
        molds_by_process.setdefault(m.get("process_code"), []).append(m)
        code = m.get("code")
        if code and code not in mold_eye_by_code:
            mold_eye_by_code[code] = _positive_int(m.get("eye"), 1)

    return {
        "machines_by_process": machines_by_process,
        "molds_by_process": molds_by_process,
        "compat_pairs": compat_pairs,
        "product_molds": product_molds,
        "mold_eye_by_code": mold_eye_by_code,
    }


def _bucket_id_for_date(dt_date: date, time_buckets: List[Dict[str, Any]]) -> str | None:
    for tb in time_buckets:
        start = tb.get("start_date")
        end = tb.get("end_date")
        if isinstance(start, str):
            start = datetime.fromisoformat(start).date()
        if isinstance(end, str):
            end = datetime.fromisoformat(end).date()
        if start <= dt_date <= end:
            return tb.get("id")
    return None


def _rebucket_lot(lot: Dict[str, Any], time_buckets: List[Dict[str, Any]], bucket_index: Dict[str, int], offset: int) -> None:
    if not time_buckets:
        return
    bucket_id = lot.get("time_bucket_id")
    if not bucket_id and lot.get("process_start_time"):
        bucket_id = _bucket_id_for_date(datetime.fromisoformat(lot["process_start_time"]).date(), time_buckets)
    if not bucket_id or bucket_id not in bucket_index:
        return

    new_index = bucket_index[bucket_id] + offset
    new_index = max(0, min(new_index, len(time_buckets) - 1))
    new_bucket = time_buckets[new_index]
    new_bucket_id = new_bucket.get("id")
    if not new_bucket_id:
        return

    # adjust timestamps to new bucket start date while keeping time-of-day
    start_time = lot.get("process_start_time")
    end_time = lot.get("process_end_time")
    if start_time:
        start_dt = datetime.fromisoformat(start_time)
        new_start_date = new_bucket.get("start_date")
        if isinstance(new_start_date, str):
            new_start_date = datetime.fromisoformat(new_start_date).date()
        new_start = datetime.combine(new_start_date, start_dt.time())
        if end_time:
            end_dt = datetime.fromisoformat(end_time)
            dur = end_dt - start_dt
            lot["process_end_time"] = (new_start + dur).isoformat()
        lot["process_start_time"] = new_start.isoformat()

    lot["time_bucket_id"] = new_bucket_id


def _mutate_state(
    state: Dict[str, Any],
    *,
    delta_hours: float,
    iter_no: int,
    mutation_cfg: Dict[str, Any],
    problem: Dict[str, Any],
) -> Dict[str, Any]:
    time_buckets = problem.get("time_buckets", [])
    bucket_index = {tb.get("id"): i for i, tb in enumerate(time_buckets) if tb.get("id")}
    compat = _build_compat_indexes(problem)

    seed = int(mutation_cfg.get("mutation_seed") or 0)
    rng = random.Random(seed + iter_no) if seed else random.Random()

    bucket_shift = int(mutation_cfg.get("bucket_shift") or 0)
    bucket_shift_rate = float(mutation_cfg.get("bucket_shift_rate") or 0.0)
    qty_jitter_pct = float(mutation_cfg.get("qty_jitter_pct") or 0.0)
    qty_jitter_rate = float(mutation_cfg.get("qty_jitter_rate") or 0.0)
    machine_swap_rate = float(mutation_cfg.get("machine_swap_rate") or 0.0)
    mold_swap_rate = float(mutation_cfg.get("mold_swap_rate") or 0.0)

    def cavity_step_for_lot(lot_obj: Dict[str, Any]) -> int:
        ar_obj = lot_obj.get("assigned_resources", {}) or {}
        mold_code = ar_obj.get("mold")
        if not mold_code:
            return 1
        return _positive_int(compat.get("mold_eye_by_code", {}).get(mold_code), 1)

    mutated = {"lots": []}
    for lot in state.get("lots", []):
        lot_copy = dict(lot)
        ar = dict(lot_copy.get("assigned_resources", {}) or {})
        lot_copy["assigned_resources"] = ar

        # 1) time shift (existing behavior)
        if delta_hours:
            for key in ("process_start_time", "process_end_time", "setup_start_time", "setup_end_time"):
                if lot_copy.get(key):
                    lot_copy[key] = _shift_time(lot_copy[key], delta_hours)

        # 2) bucket shift (optional)
        if bucket_shift and bucket_shift_rate > 0.0 and rng.random() < bucket_shift_rate:
            shift = bucket_shift if (iter_no % 2 == 0) else -bucket_shift
            _rebucket_lot(lot_copy, time_buckets, bucket_index, shift)

        # 3) quantity jitter (optional)
        if qty_jitter_pct > 0.0 and qty_jitter_rate > 0.0 and rng.random() < qty_jitter_rate:
            direction = 1.0 if (rng.random() >= 0.5) else -1.0
            factor = max(0.0, 1.0 + direction * qty_jitter_pct)
            lot_copy["qty"] = max(0.0, float(lot_copy.get("qty", 0.0)) * factor)

        # 4) machine swap (optional)
        if machine_swap_rate > 0.0 and rng.random() < machine_swap_rate:
            process_code = lot_copy.get("process_code")
            mold_code = ar.get("mold")
            candidates = compat["machines_by_process"].get(process_code, [])
            if mold_code:
                candidates = [
                    m for m in candidates
                    if (m.get("id"), mold_code, process_code) in compat["compat_pairs"]
                    or (m.get("id"), mold_code, None) in compat["compat_pairs"]
                ]
            if candidates:
                cur = ar.get("machine")
                alt = [m for m in candidates if m.get("id") != cur]
                if alt:
                    ar["machine"] = rng.choice(alt).get("id")

        # 5) mold swap (optional)
        if mold_swap_rate > 0.0 and rng.random() < mold_swap_rate:
            process_code = lot_copy.get("process_code")
            machine_id = ar.get("machine")
            pcode = lot_copy.get("product_code")
            allowed = compat["product_molds"].get((pcode, process_code))
            candidates = compat["molds_by_process"].get(process_code, [])
            if allowed is not None:
                candidates = [m for m in candidates if m.get("code") in allowed]
            if machine_id:
                candidates = [
                    m for m in candidates
                    if (machine_id, m.get("code"), process_code) in compat["compat_pairs"]
                    or (machine_id, m.get("code"), None) in compat["compat_pairs"]
                ]
            if candidates:
                cur = ar.get("mold")
                alt = [m for m in candidates if m.get("code") != cur]
                if alt:
                    ar["mold"] = rng.choice(alt).get("code")

        # enforce discrete lot quantities based on mold eye/cavity count
        if "qty" in lot_copy:
            lot_copy["qty"] = _quantize_qty_to_cavity(lot_copy.get("qty", 0.0), cavity_step_for_lot(lot_copy))

        mutated["lots"].append(lot_copy)

    return mutated


def _mutation_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "time_shift_hours": float(payload.get("time_shift_hours", 0.0)),
        "bucket_shift": int(payload.get("bucket_shift", 0)),
        "bucket_shift_rate": float(payload.get("bucket_shift_rate", 0.0)),
        "qty_jitter_pct": float(payload.get("qty_jitter_pct", 0.0)),
        "qty_jitter_rate": float(payload.get("qty_jitter_rate", 0.0)),
        "machine_swap_rate": float(payload.get("machine_swap_rate", 0.0)),
        "mold_swap_rate": float(payload.get("mold_swap_rate", 0.0)),
        "mutation_seed": int(payload.get("mutation_seed", 0)),
    }


def planned_iterations(payload: Dict[str, Any]) -> int:
    max_iter = int(payload.get("max_iter", 5))
    return 1 + max_iter


def _ga_search(
    ctx: AlgorithmContext,
    *,
    start_state: Dict[str, Any],
    start_eval: Dict[str, Any],
    record_base: bool,
    base_label: str = "greedy",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if record_base:
        ctx.record(start_state, start_eval, base_label)

    cur_best_state = start_state
    cur_best_eval = start_eval

    payload = ctx.payload
    mutation_cfg = _mutation_config(payload)
    max_iter = int(payload.get("max_iter", 5))
    time_shift_hours = float(mutation_cfg.get("time_shift_hours", 0.0))

    for step_idx in range(1, max_iter + 1):
        delta = time_shift_hours if (step_idx % 2 == 1) else -time_shift_hours
        mutated = _mutate_state(
            cur_best_state,
            delta_hours=delta,
            iter_no=step_idx,
            mutation_cfg=mutation_cfg,
            problem=ctx.problem,
        )
        m_eval = ctx.evaluate(mutated)
        ctx.record(mutated, m_eval, f"ga-{step_idx}")
        if is_better(m_eval, cur_best_eval):
            cur_best_state, cur_best_eval = mutated, m_eval

    return cur_best_state, cur_best_eval


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state, base_eval, base_label = select_initial_state(ctx)
    return _ga_search(ctx, start_state=base_state, start_eval=base_eval, record_base=True, base_label=base_label)


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="ga",
        name="GA",
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


__all__ = ["ALGO", "_ga_search"]
