# TR: API rotalarini ve HTTP is akisini tanimlar.
# EN: Defines API routes and HTTP workflow.
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.evaluation.evaluator import evaluate_frame
from app.evaluation.problem_validator import validate_references
from app.frame.ingest.problem_adapter import load_problem_frame
from app.frame.models.problem import ProblemFrame, State
from app.frame.services.frame_manager import FrameManager
from app.optimization.optimizer import optimize_frame


router = APIRouter()
manager = FrameManager()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/frame")
def create_frame(payload: dict = Body(...)) -> dict:
    try:
        problem_frame: ProblemFrame = load_problem_frame(payload)
        frame_id = manager.save(problem_frame)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": frame_id, "frame": problem_frame}


@router.get("/frame/{frame_id}")
def get_frame(frame_id: str) -> ProblemFrame:
    frame = manager.get(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    return frame


@router.post("/frame/{frame_id}/state")
def update_state(frame_id: str, payload: dict = Body(...)) -> ProblemFrame:
    frame = manager.get(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    try:
        state = State.model_validate(payload)
        updated = manager.update_state(frame_id, state)
        return updated
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/frame/{frame_id}/validate")
def validate_frame(frame_id: str) -> dict:
    frame = manager.get(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    errors = validate_references(frame)
    return {"valid": not errors, "errors": errors}


@router.post("/frame/{frame_id}/evaluate")
def evaluate(frame_id: str) -> dict:
    frame = manager.get(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    return evaluate_frame(frame)


@router.post("/frame/{frame_id}/optimize")
def optimize(frame_id: str, payload: dict = Body(default=None)) -> dict:
    frame = manager.get(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    try:
        return optimize_frame(frame, payload or {})
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
