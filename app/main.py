# TR: FastAPI giris noktasi.
# EN: FastAPI entrypoint.
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

app = FastAPI(title="Heuristic Production Planning API", version="0.1.0")
app.include_router(router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def ui_root() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/ui")
def ui_alias() -> FileResponse:
    return FileResponse("app/static/index.html")
