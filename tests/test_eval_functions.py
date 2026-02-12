from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.evaluation import eval_core
from app.frame.ingest.normalizer import normalize_problem_frame


DATA_DIR = Path(__file__).parent / "data"


def _load_violation_frame() -> dict:
    return json.loads((DATA_DIR / "eval_violation.json").read_text(encoding="utf-8"))


@pytest.mark.api
def test_all_constraint_evaluators_trigger_expected_violations():
    raw = _load_violation_frame()
    norm = normalize_problem_frame(raw)

    ctx = eval_core.build_evaluation_context(
        norm["problemData"],
        norm["state"],
        norm["scenarioConfig"],
    )
    res = eval_core.evaluate_constraints(ctx)

    hard = {item["code"]: item["value"] for item in res["hard"]["items"]}
    soft = {t["code"]: t["value"] for t in res["soft"]["terms"]}

    # Hard constraints should all be violated (>0)
    expected_hard = [
        "HARD_DUE_DATE_FULFILLMENT",
        "HARD_RESOURCE_ROLE_ASSIGNED",
        "HARD_TIME_BUCKET_VALID",
        "HARD_NO_HOLIDAY_WORK",
        "HARD_COMPAT_MACHINE_MOLD_PROCESS",
        "HARD_COMPAT_PRODUCT_MOLD",
        "HARD_CAPACITY_BUCKET",
        "HARD_CAPACITY_SEGMENT",
        "HARD_MACHINE_TIME_OVERLAP",
    ]
    for code in expected_hard:
        assert hard.get(code, 0) > 0, f"{code} should be positive"

    # Soft KPIs should register mold changes
    assert soft.get("SOFT_MOLD_CHANGE_MINIMIZE", 0) >= 2
    assert soft.get("SOFT_NIGHT_MOLD_CHANGE", 0) >= 1
