from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.frame.ingest.normalizer import normalize_problem_frame
from app.frame.ingest.problem_adapter import load_problem_frame
from app.evaluation.evaluate_state import evaluate_state


DATA_DIR = Path(__file__).parent / "data"


def _load_raw() -> dict:
    return json.loads((DATA_DIR / "problemFrame.json").read_text(encoding="utf-8"))


def test_normalizer_resources_and_orders():
    raw = _load_raw()
    norm = normalize_problem_frame(raw)
    problem = norm["problemData"]

    assert "machines" in problem["resources"]
    assert "molds" in problem["resources"]
    assert problem["resources"]["molds"][0]["code"] == "KLP_P1_01"

    orders = problem["orders"][0]["orders"][0]
    assert orders.get("time_bucket_id") == "CW43_25"
    assert orders.get("due_date") is not None


def test_state_assigned_resources_and_bucket():
    raw = _load_raw()
    norm = normalize_problem_frame(raw)
    lot = norm["state"]["lots"][0]
    assert lot["assigned_resources"]["machine"] == 12
    assert lot["assigned_resources"]["mold"] == "KLP_P1_01"
    assert lot["time_bucket_id"] == "CW43_25"


def test_evaluate_state_runs_and_penalty_night_change():
    raw = _load_raw()
    norm = normalize_problem_frame(raw)

    # create two NIGHT lots with mold change on same machine
    l1 = {
        "lot_id": "N1",
        "product_code": "P1",
        "process_code": "AP300",
        "time_bucket_id": "CW43_25",
        "qty": 1000,
        "process_start_time": "2025-11-26T00:10:00",
        "process_end_time": "2025-11-26T01:10:00",
        "assigned_resources": {"machine": 12, "mold": "KLP_P1_01"},
    }
    l2 = {
        "lot_id": "N2",
        "product_code": "P1",
        "process_code": "AP300",
        "time_bucket_id": "CW43_25",
        "qty": 1000,
        "process_start_time": "2025-11-26T01:30:00",
        "process_end_time": "2025-11-26T02:30:00",
        "assigned_resources": {"machine": 12, "mold": "KLP_P1_02"},
    }

    norm["state"]["lots"] = [l1, l2]

    res = evaluate_state(
        state=norm["state"],
        problemData=norm["problemData"],
        scenarioConfig=norm["scenarioConfig"],
    )
    assert res["constraint_results"]["SOFT_NIGHT_MOLD_CHANGE"]["violation"] >= 1
