# TR: Harici JSON verisini ic modele normalize eder ve yukler.
# EN: Normalizes external JSON into internal model and loads it.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Union

from pydantic import ValidationError

from app.frame.models.problem import ProblemFrame


def _normalize_constraints(raw_scenario: Dict[str, Any]) -> Dict[str, Any]:
    constraints = raw_scenario.get("constraints")
    if constraints is None:
        raw_scenario["constraints"] = []
        return raw_scenario

    # Support both dict (code: props) and list formats.
    if isinstance(constraints, dict):
        normalized = []
        for code, payload in constraints.items():
            if not isinstance(payload, dict):
                continue
            entry = {"code": code, **payload}
            entry.setdefault("active", True)
            if entry.get("time_scope") is None and entry.get("shift_based") is not None:
                entry["time_scope"] = "SHIFT" if entry["shift_based"] else "WEEK"
            normalized.append(entry)
        raw_scenario["constraints"] = normalized
    elif isinstance(constraints, list):
        raw_scenario["constraints"] = constraints
    else:
        raw_scenario["constraints"] = []
    return raw_scenario


def load_problem_frame(data_or_path: Union[str, Path, Dict[str, Any]]) -> ProblemFrame:
    if isinstance(data_or_path, (str, Path)):
        raw = json.loads(Path(data_or_path).read_text(encoding="utf-8"))
    elif isinstance(data_or_path, dict):
        raw = data_or_path
    else:
        raise ValueError("Unsupported input type for problem frame loader.")

    raw["scenarioConfig"] = _normalize_constraints(raw.get("scenarioConfig", {}))
    state = raw.get("state", {})
    if isinstance(state, dict):
        lots = state.get("lots")
        plan = state.get("plan")
        if isinstance(lots, list) and lots:
            sample = lots[0]
            # Heuristic: inventory rows have opening_stock/closing_stock fields.
            if isinstance(sample, dict) and "opening_stock" in sample:
                state.setdefault("inventory", lots)
                if isinstance(plan, list):
                    state["lots"] = plan
        if "lots" not in state and isinstance(plan, list):
            state["lots"] = plan
        raw["state"] = state

    try:
        return ProblemFrame.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
