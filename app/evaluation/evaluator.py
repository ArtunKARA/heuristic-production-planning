# TR: Problem cercevesini kisit ve KPI acisindan degerlendirir.
# EN: Evaluates the problem frame for constraints and KPIs.
from __future__ import annotations

from typing import Dict

from app.evaluation.problem_validator import validate_references
from app.frame.models.problem import ProblemFrame


def evaluate_frame(frame: ProblemFrame) -> Dict[str, object]:
    errors = validate_references(frame)
    total_qty = sum(item.qty for item in frame.state.lots)
    return {
        "valid": not errors,
        "errors": errors,
        "kpis": {
            "lots_count": len(frame.state.lots),
            "inventory_rows": len(frame.state.inventory),
            "total_qty": total_qty,
        },
    }
