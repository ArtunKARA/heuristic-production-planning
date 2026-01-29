"""Canonical JSON normalizer/adapter.

Bridges current example JSON with the canonical model expected by eval_core:
 - resources.machine -> resources.machines, resources.mold -> resources.molds
 - mold id fallback: id=code if absent; ensure code str
 - orders: week -> time_bucket_id; parse due_date; ensure qty present
 - process steps: process_input -> inputs
 - time buckets/work calendar: parse ISO dates
 - shift templates: ensure S3 has NIGHT/MORNING/EVENING segments
 - state lots: resources list -> assigned_resources map; rename week->time_bucket_id
 - convert date/datetime strings to objects where needed
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Dict, List


def _parse_date(val: Any) -> Any:
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        return datetime.fromisoformat(val).date()
    return val


def _parse_datetime(val: Any) -> Any:
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, str):
        return datetime.fromisoformat(val)
    return val


def _normalize_orders(problem: Dict[str, Any]) -> None:
    for og in problem.get("orders", []):
        for o in og.get("orders", []):
            if "time_bucket_id" not in o and "week" in o:
                o["time_bucket_id"] = o.get("week")
            if o.get("due_date") is None and o.get("time_bucket_id") and problem.get("time_buckets"):
                # map bucket end_date as due_date 23:59:59
                tb = next((tb for tb in problem["time_buckets"] if tb["id"] == o["time_bucket_id"]), None)
                if tb and tb.get("end_date"):
                    end_dt = datetime.combine(tb["end_date"], datetime.max.time()).replace(microsecond=0)
                    o["due_date"] = end_dt
            if isinstance(o.get("due_date"), str):
                o["due_date"] = _parse_datetime(o["due_date"])


def _normalize_process_steps(problem: Dict[str, Any]) -> None:
    for p in problem.get("products", []):
        for step in p.get("process_data", []):
            if not step.get("inputs") and step.get("process_input"):
                step["inputs"] = step.get("process_input", [])


def _normalize_resources(problem: Dict[str, Any]) -> None:
    res = problem.get("resources", {})
    # rename machine/mold to plural keys
    if "machines" not in res and "machine" in res:
        res["machines"] = res.get("machine", [])
    if "molds" not in res and "mold" in res:
        res["molds"] = res.get("mold", [])

    for m in res.get("machines", []):
        # capacity key alias
        if "capacity_by_bucket" not in m and "weekly_capacity" in m:
            m["capacity_by_bucket"] = m.get("weekly_capacity", {})
        # ids should stay int where possible
        if isinstance(m.get("id"), str) and m["id"].isdigit():
            m["id"] = int(m["id"])

    for mold in res.get("molds", []):
        if "code" not in mold and mold.get("id") is not None:
            mold["code"] = str(mold["id"])
        if isinstance(mold.get("id"), int) and "code" not in mold:
            mold["code"] = str(mold["id"])
        mold.setdefault("id", mold.get("code"))
        # normalize compatible_machines to ints where possible
        compat = mold.get("compatible_machines_id") or mold.get("compatible_machines") or []
        compat_norm: List[int] = []
        for mid in compat:
            if isinstance(mid, str) and mid.isdigit():
                compat_norm.append(int(mid))
            else:
                compat_norm.append(mid)
        mold["compatible_machines_id"] = compat_norm


def _normalize_time_layers(problem: Dict[str, Any]) -> None:
    for tb in problem.get("time_buckets", []):
        if isinstance(tb.get("start_date"), str):
            tb["start_date"] = _parse_date(tb["start_date"])
        if isinstance(tb.get("end_date"), str):
            tb["end_date"] = _parse_date(tb["end_date"])

    # Work calendar
    wc = problem.get("work_calendar") or []
    for w in wc:
        if isinstance(w.get("date"), str):
            w["date"] = _parse_date(w["date"])

    # Ensure S3 segments complete if present
    for st in problem.get("shift_templates", []):
        if st.get("code") == "S3" and len(st.get("segments", [])) < 3:
            st["segments"] = [
                {"code": "NIGHT", "start": "00:00", "end": "08:00", "constraints": ["NO_MOLD_CHANGE_AT_NIGHT"]},
                {"code": "MORNING", "start": "08:00", "end": "16:00", "constraints": []},
                {"code": "EVENING", "start": "16:00", "end": "00:00", "constraints": []},
            ]


def _normalize_state(state: Dict[str, Any]) -> None:
    lots = state.get("lots") or state.get("plan") or []
    for lot in lots:
        if "time_bucket_id" not in lot and "week" in lot:
            lot["time_bucket_id"] = lot.get("week")
        # resources list -> assigned_resources map
        assigned = lot.get("assigned_resources") or {}
        for res in lot.get("resources", []):
            if isinstance(res, dict) and res.get("type") and res.get("id") is not None:
                assigned[res["type"]] = res["id"]
        lot["assigned_resources"] = assigned
        for key in ("setup_start_time", "setup_end_time", "process_start_time", "process_end_time"):
            if lot.get(key):
                lot[key] = _parse_datetime(lot[key])

    # ensure lots live under "lots"
    state["lots"] = lots

    # move inventory rows into inventory_summary if present in lots
    if lots:
        sample = lots[0]
        if isinstance(sample, dict) and "opening_stock" in sample:
            state["inventory_summary"] = lots
            state["lots"] = state.get("plan") or []


def normalize_problem_frame(raw: Dict[str, Any]) -> Dict[str, Any]:
    data = deepcopy(raw)

    problem = data.get("problemData") or data.get("problem") or {}
    state = data.get("state") or {}
    scenario = data.get("scenarioConfig") or data.get("scenario") or {}

    _normalize_time_layers(problem)
    _normalize_resources(problem)
    _normalize_orders(problem)
    _normalize_process_steps(problem)
    _normalize_state(state)

    # scenario toggles/weights fallback if constraints list present
    if "constraints" in scenario and ("toggles" not in scenario or "weights" not in scenario):
        toggles = scenario.get("toggles", {})
        weights = scenario.get("weights", {})
        for c in scenario.get("constraints", []):
            code = c.get("code")
            if not code:
                continue
            mapped = code_mapping().get(code, code)
            if c.get("type") == "hard":
                toggles[mapped] = c.get("active", True)
            else:
                toggles[mapped] = c.get("active", True)
                if c.get("weight") is not None:
                    wk = weight_key_for_code(mapped)
                    if wk:
                        weights[wk] = c.get("weight")
        scenario.setdefault("toggles", toggles)
        scenario.setdefault("weights", weights)

    data["problemData"] = problem
    data["state"] = state
    data["scenario"] = scenario
    data["scenarioConfig"] = scenario
    data["problem"] = problem
    return data


def code_mapping() -> Dict[str, str]:
    """Constraint code mapping table from draw.io naming to evaluator naming."""
    return {
        "DEMAND_SATISFACTION_PER_WEEK": "HARD_DUE_DATE_FULFILLMENT",
        "NO_MOLD_CHANGE_AT_NIGHT": "SOFT_NIGHT_MOLD_CHANGE",
        # SHIFT_TEMPLATES not implemented -> ignored
    }


def weight_key_for_code(code: str) -> str | None:
    lookup = {
        "SOFT_MOLD_CHANGE_MINIMIZE": "w_mold_change",
        "SOFT_NIGHT_MOLD_CHANGE": "w_night_mold_change",
        "SOFT_INVENTORY_LOW": "w_inventory",
        "SOFT_MACHINE_COUNT_LOW": "w_machine_count",
    }
    return lookup.get(code)


__all__ = ["normalize_problem_frame", "code_mapping"]
