from __future__ import annotations

"""
TR: API ve veri modeli icin ornek senaryo testlerini calistirir.
EN: Runs sample scenario tests for the API and data model.
"""

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.frame.ingest.problem_adapter import load_problem_frame
from app.main import app
from app.frame.repositories.problem_repo import ProblemRepository
from app.evaluation.problem_validator import validate_references


DATA_DIR = Path(__file__).parent / "data"
API_CLIENT = TestClient(app)
_FRAME_ID: str | None = None


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def deep_copy(payload: dict) -> dict:
    return json.loads(json.dumps(payload))


def scenario_basic_validation() -> None:
    # TR: Ornek verinin model ve referans dogrulamasindan gectigini test eder.
    # EN: Ensures the sample data passes model and reference validation.
    payload = load_json(DATA_DIR / "problemFrame.json")
    frame = load_problem_frame(payload)
    errors = validate_references(frame)
    if errors:
        raise AssertionError(f"Validation errors: {errors}")


def scenario_api_post_get() -> None:
    # TR: /frame olusturma ve /frame/{id} okuma akisini test eder.
    # EN: Tests create/read flow for /frame and /frame/{id}.
    client = API_CLIENT
    pid = _get_frame_id()
    resp2 = client.get(f"/frame/{pid}")
    if resp2.status_code != 200:
        raise AssertionError(f"GET /frame/{{id}} failed: {resp2.text}")


def scenario_api_validate() -> None:
    # TR: /frame/{id}/validate endpointinin basarili calistigini test eder.
    # EN: Tests that /frame/{id}/validate works and returns valid=true.
    client = API_CLIENT
    pid = _get_frame_id()
    resp2 = client.post(f"/frame/{pid}/validate")
    if resp2.status_code != 200:
        raise AssertionError(f"POST /frame/{{id}}/validate failed: {resp2.text}")
    if not resp2.json().get("valid", False):
        raise AssertionError(f"Expected valid frame, got: {resp2.text}")


def scenario_api_evaluate() -> None:
    # TR: /frame/{id}/evaluate endpointinden KPI dondugunu test eder.
    # EN: Tests that /frame/{id}/evaluate returns KPI payload.
    client = API_CLIENT
    pid = _get_frame_id()
    resp2 = client.post(f"/frame/{pid}/evaluate")
    if resp2.status_code != 200:
        raise AssertionError(f"POST /frame/{{id}}/evaluate failed: {resp2.text}")
    if "kpis" not in resp2.json():
        raise AssertionError(f"Expected KPI payload, got: {resp2.text}")


def _get_frame_id() -> str:
    # TR: API testleri icin tek frame olusturur ve tekrar kullanir.
    # EN: Creates a single frame for API tests and reuses it.
    global _FRAME_ID
    if _FRAME_ID:
        return _FRAME_ID
    payload = load_json(DATA_DIR / "problemFrame.json")
    resp = API_CLIENT.post("/frame", json=payload)
    if resp.status_code != 200:
        raise AssertionError(f"POST /frame failed: {resp.text}")
    _FRAME_ID = resp.json().get("id")
    return _FRAME_ID


def scenario_invalid_process_code() -> None:
    # TR: Gecersiz process_code icin hata donmesini test eder.
    # EN: Tests validation error for an invalid process_code.
    payload = load_json(DATA_DIR / "problemFrame.json")
    payload = deep_copy(payload)
    payload["problemData"]["products"][0]["process_data"][0]["process_code"] = "AP999"
    frame = load_problem_frame(payload)
    errors = validate_references(frame)
    if not any("unknown process AP999" in err for err in errors):
        raise AssertionError(f"Expected unknown process error, got: {errors}")


def scenario_invalid_product_code_in_orders() -> None:
    # TR: Sipariste gecersiz product_code icin hata donmesini test eder.
    # EN: Tests validation error for an invalid product_code in orders.
    payload = load_json(DATA_DIR / "problemFrame.json")
    payload = deep_copy(payload)
    payload["problemData"]["orders"][0]["product_code"] = "PX"
    frame = load_problem_frame(payload)
    errors = validate_references(frame)
    if not any("orders reference unknown product PX" in err for err in errors):
        raise AssertionError(f"Expected unknown product error, got: {errors}")


def scenario_invalid_time_bucket_in_orders() -> None:
    # TR: Sipariste gecersiz week/time_bucket icin hata donmesini test eder.
    # EN: Tests validation error for an invalid week/time_bucket in orders.
    payload = load_json(DATA_DIR / "problemFrame.json")
    payload = deep_copy(payload)
    payload["problemData"]["orders"][0]["orders"][0]["week"] = "CW00_00"
    frame = load_problem_frame(payload)
    errors = validate_references(frame)
    if not any("orders reference unknown time bucket CW00_00" in err for err in errors):
        raise AssertionError(f"Expected unknown time bucket error, got: {errors}")


def scenario_invalid_time_bucket_in_plan() -> None:
    # TR: Planda gecersiz week/time_bucket icin hata donmesini test eder.
    # EN: Tests validation error for an invalid week/time_bucket in plan.
    payload = load_json(DATA_DIR / "problemFrame.json")
    payload = deep_copy(payload)
    payload["state"]["plan"][0]["week"] = "CW00_00"
    frame = load_problem_frame(payload)
    errors = validate_references(frame)
    if not any("plan L1 references unknown time bucket CW00_00" in err for err in errors):
        raise AssertionError(f"Expected plan time bucket error, got: {errors}")


def scenario_incompatible_machine_mold() -> None:
    # TR: Uyumlu olmayan machine/mold kombinasyonunu test eder.
    # EN: Tests incompatible machine/mold combination handling.
    payload = load_json(DATA_DIR / "problemFrame.json")
    payload = deep_copy(payload)
    payload["state"]["plan"][0]["resources"].append({"type": "mold", "id": "KLP_P1_02"})
    frame = load_problem_frame(payload)
    errors = validate_references(frame)
    if not any("incompatible machine/mold/process" in err for err in errors):
        raise AssertionError(f"Expected machine/mold compatibility error, got: {errors}")


def scenario_constraints_dict_normalization() -> None:
    # TR: constraints dict formatinin listeye normalize edildigini test eder.
    # EN: Tests dict-to-list normalization for constraints.
    payload = load_json(DATA_DIR / "problemFrame.json")
    payload = deep_copy(payload)
    payload["scenarioConfig"]["constraints"] = {
        "DEMAND_SATISFACTION_PER_WEEK": {"type": "hard", "shift_based": False},
        "NO_MOLD_CHANGE_AT_NIGHT": {"type": "soft", "weight": 5.0, "shift_based": True},
    }
    frame = load_problem_frame(payload)
    if not frame.scenarioConfig.constraints:
        raise AssertionError("Expected constraints list after normalization")


def scenario_repository_save_load() -> None:
    # TR: Repository save/load serilestirme akisini test eder.
    # EN: Tests repository save/load serialization flow.
    payload = load_json(DATA_DIR / "problemFrame.json")
    frame = load_problem_frame(payload)
    with tempfile.TemporaryDirectory() as tmp:
        repo = ProblemRepository(base_path=tmp)
        repo.save("TMP_01", frame)
        loaded = repo.load("TMP_01")
        if loaded is None:
            raise AssertionError("Expected repository load to return data")
        if loaded.problemData.problem_meta.problem_code != frame.problemData.problem_meta.problem_code:
            raise AssertionError("Repository load mismatch on problem_code")


if __name__ == "__main__":
    scenarios = [
        ("basic_validation", scenario_basic_validation),
        ("api_post_get", scenario_api_post_get),
        ("api_validate", scenario_api_validate),
        ("api_evaluate", scenario_api_evaluate),
        ("invalid_process_code", scenario_invalid_process_code),
        ("invalid_product_code_in_orders", scenario_invalid_product_code_in_orders),
        ("invalid_time_bucket_in_orders", scenario_invalid_time_bucket_in_orders),
        ("invalid_time_bucket_in_plan", scenario_invalid_time_bucket_in_plan),
        ("incompatible_machine_mold", scenario_incompatible_machine_mold),
        ("constraints_dict_normalization", scenario_constraints_dict_normalization),
        ("repository_save_load", scenario_repository_save_load),
    ]
    for name, fn in scenarios:
        fn()
        print(f"{name}: ok")
    print("test done")
