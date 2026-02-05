# TR: API rotalarini ve HTTP is akisini tanimlar.
# EN: Defines API routes and HTTP workflow.
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.evaluation.evaluator import evaluate_frame
from app.evaluation.problem_validator import validate_references
from app.frame.ingest.problem_adapter import load_problem_frame
from app.frame.models.problem import ProblemFrame, State
from app.frame.services.frame_manager import FrameManager
from app.optimization.optimizer import optimize_frame, list_algorithms
from fastapi.responses import StreamingResponse
import asyncio
import threading
import json


router = APIRouter()
manager = FrameManager()


def _algorithms_for_problem(problem_data: dict | None) -> list[dict]:
    return list_algorithms(problem_data)


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


# -------------------------
# SSE API (grouped under /sse)
# -------------------------

@router.post("/sse/frame")
def sse_create_frame(payload: dict = Body(...)) -> dict:
    """
    Accepts problem payload from user and returns frame id + available algorithms.
    """
    try:
        problem_frame: ProblemFrame = load_problem_frame(payload)
        frame_id = manager.save(problem_frame)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    algos = _algorithms_for_problem(problem_frame.problemData.model_dump(mode="json", by_alias=True))
    return {"id": frame_id, "algorithms": algos}


@router.post("/sse/algorithms")
def sse_algorithms(payload: dict = Body(default=None)) -> dict:
    """
    Returns algorithms list based on provided problemData (optional).
    """
    problem = (payload or {}).get("problemData") or (payload or {}).get("problem") or {}
    return {"algorithms": _algorithms_for_problem(problem)}


@router.post("/sse/optimize/stream")
async def sse_optimize_stream(payload: dict = Body(default=None)) -> StreamingResponse:
    """
    SSE optimizer:
      payload: {frame_id, strategy, max_iter}
    Emits:
      - type: "meta" (initial info)
      - type: "iteration" (each iteration)
      - type: "done" (final result)
      - type: "error" (if fails)
    """
    data = payload or {}
    frame_id = data.get("frame_id")
    if not frame_id:
        raise HTTPException(status_code=400, detail="frame_id is required")
    frame = manager.get(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    strategy = (data.get("strategy") or "greedy").lower()
    max_iter = int(data.get("max_iter") or 5)

    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()

    def sink(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, event)

    def worker():
        try:
            res = optimize_frame(frame, {"strategy": strategy, "max_iter": max_iter}, event_sink=sink)
            loop.call_soon_threadsafe(q.put_nowait, {"type": "done", "result": res})
        except Exception as exc:  # keep error event in stream
            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})

    threading.Thread(target=worker, daemon=True).start()

    async def event_gen():
        yield f"data: {json.dumps({'type': 'meta', 'strategy': strategy, 'max_iter': max_iter})}\n\n"
        while True:
            ev = await q.get()
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") in ("done", "error"):
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream")

@router.get("/frame/{frame_id}/optimize/stream")
async def optimize_stream(frame_id: str, strategy: str = "greedy", max_iter: int = 5) -> StreamingResponse:
    frame = manager.get(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()

    def sink(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, event)

    def worker():
        res = optimize_frame(frame, {"strategy": strategy, "max_iter": max_iter}, event_sink=sink)
        loop.call_soon_threadsafe(q.put_nowait, {"type": "done", "result": res})

    threading.Thread(target=worker, daemon=True).start()

    async def event_gen():
        while True:
            ev = await q.get()
            if ev.get("type") == "done":
                yield f"data: {json.dumps(ev)}\n\n"
                break
            else:
                yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/frame/{frame_id}/optimize/streaming")
async def optimize_stream_post(frame_id: str, payload: dict = Body(default=None)) -> StreamingResponse:
    strategy = (payload or {}).get("strategy", "greedy")
    max_iter = int((payload or {}).get("max_iter", 5))
    return await optimize_stream(frame_id, strategy=strategy, max_iter=max_iter)
