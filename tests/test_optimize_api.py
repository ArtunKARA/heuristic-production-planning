from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.main import app
from app.frame.ingest.problem_adapter import load_problem_frame


API_CLIENT = TestClient(app)
DATA_DIR = Path(__file__).parent / "data"


def _load_frame_payload() -> dict:
    return json.loads((DATA_DIR / "problemFrame.json").read_text(encoding="utf-8"))


@pytest.mark.api
def test_optimize_endpoint_selects_best_candidate():
    frame_payload = _load_frame_payload()
    # create frame
    resp = API_CLIENT.post("/frame", json=frame_payload)
    assert resp.status_code == 200
    frame_id = resp.json()["id"]

    # two candidates: good (feasible), bad (missing mold)
    def lot(lot_id: str, dt: str):
        return {
            "lot_id": lot_id,
            "product_code": "P1",
            "process_code": "AP300",
            "time_bucket_id": "CW43_25",
            "qty": 5500,
            "process_start_time": dt,
            "process_end_time": "2025-11-24T08:00:00",
            "assigned_resources": {"machine": 12, "mold": "KLP_P1_01"},
        }

    good_state = {
        "lots": [
            lot("G1", "2025-11-24T00:00:00"),
            lot("G2", "2025-11-24T08:00:00"),
            lot("G3", "2025-11-24T16:00:00"),
            lot("G4", "2025-11-25T00:00:00"),
            lot("G5", "2025-11-25T08:00:00"),
            lot("G6", "2025-11-25T16:00:00"),
        ]
    }

    # Strategy-based optimize (greedy); ignores custom candidates now
    resp2 = API_CLIENT.post(f"/frame/{frame_id}/optimize", json={"strategy": "greedy"})
    assert resp2.status_code == 200, resp2.text
    body = resp2.json()
    assert body["best_index"] == 0
    assert len(body.get("iterations", [])) >= 1
    # iteration payload should include full evaluation snapshot
    assert "evaluation" in body["iterations"][0]
    assert "constraint_results" in body["iterations"][0]["evaluation"]


@pytest.mark.api
def test_optimize_endpoint_supports_hho_strategy():
    frame_payload = _load_frame_payload()
    resp = API_CLIENT.post("/frame", json=frame_payload)
    assert resp.status_code == 200
    frame_id = resp.json()["id"]

    resp2 = API_CLIENT.post(
        f"/frame/{frame_id}/optimize",
        json={
            "strategy": "hho",
            "max_iter": 3,
            "hho_hawks": 4,
            "mutation_seed": 7,
        },
    )
    assert resp2.status_code == 200, resp2.text
    body = resp2.json()

    assert len(body.get("iterations", [])) == 4
    assert body["iterations"][0]["progress"]["label"] in {"greedy", "state"}
    assert body["iterations"][1]["progress"]["label"] == "hho-1"
    assert "best_state" in body and "best_evaluation" in body
