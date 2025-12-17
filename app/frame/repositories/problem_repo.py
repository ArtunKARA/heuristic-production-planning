# TR: ProblemFrame verisini diske kaydeder/okur.
# EN: Persists ProblemFrame to disk and loads it.
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.frame.models.problem import ProblemFrame


class ProblemRepository:
    def __init__(self, base_path: Path | str = "data") -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save(self, problem_id: str, frame: ProblemFrame) -> Path:
        path = self.base_path / f"{problem_id}.json"
        payload = frame.model_dump(mode="json", by_alias=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, problem_id: str) -> Optional[ProblemFrame]:
        path = self.base_path / f"{problem_id}.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ProblemFrame.model_validate(raw)
