# TR: Problem cercevesini kisit ve KPI acisindan degerlendirir.
# EN: Evaluates the problem frame for constraints and KPIs.
from __future__ import annotations

from typing import Dict

from app.evaluation.evaluate_state import evaluate_state
from app.evaluation.problem_validator import validate_references
from app.frame.models.problem import ProblemFrame


def evaluate_frame(frame: ProblemFrame) -> Dict[str, object]:
    errors = validate_references(frame)
    if errors:
        return {"valid": False, "errors": errors}

    # Use canonical evaluator
    res = evaluate_state(
        state=frame.state.model_dump(mode="json", by_alias=True),
        problemData=frame.problemData.model_dump(mode="json", by_alias=True),
        scenarioConfig=frame.scenarioConfig.model_dump(mode="json", by_alias=True),
    )

    return {
        "valid": True,
        "errors": [],
        "feasible": res["feasible"],
        "total_cost": res["total_cost"],
        "hard_total": res.get("hard_total", 0.0),
        "hard_penalty_weight": res.get("hard_penalty_weight", 1.0),
        "total_score": res.get("total_score", res["total_cost"]),
        "constraint_results": res["constraint_results"],
        "kpi_results": res["kpi_results"],
    }
