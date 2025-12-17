# TR: ProblemFrame kaydetme/getirme ve state guncelleme servisi.
# EN: Service for saving/getting ProblemFrame and updating state.
from __future__ import annotations

import uuid
from typing import Dict, Optional

from app.frame.models.problem import ProblemFrame, State
from app.frame.repositories.problem_repo import ProblemRepository
from app.evaluation.problem_validator import validate_references


class FrameManager:
    def __init__(self, repository: Optional[ProblemRepository] = None) -> None:
        self._repo = repository or ProblemRepository()
        self._store: Dict[str, ProblemFrame] = {}

    def save(self, frame: ProblemFrame, problem_id: Optional[str] = None) -> str:
        errors = validate_references(frame)
        if errors:
            raise ValueError(f"Validation errors: {errors}")

        base_id = problem_id or frame.problemData.problem_meta.problem_code or uuid.uuid4().hex
        problem_id = base_id
        if problem_id in self._store or self._repo.load(problem_id) is not None:
            problem_id = f"{base_id}_{uuid.uuid4().hex[:8]}"
        self._store[problem_id] = frame
        self._repo.save(problem_id, frame)
        return problem_id

    def get(self, problem_id: str) -> Optional[ProblemFrame]:
        if problem_id in self._store:
            return self._store[problem_id]
        loaded = self._repo.load(problem_id)
        if loaded:
            self._store[problem_id] = loaded
        return loaded

    def update_state(self, problem_id: str, state: State) -> ProblemFrame:
        frame = self.get(problem_id)
        if frame is None:
            raise KeyError(f"Problem {problem_id} not found")
        frame.state = state
        self._repo.save(problem_id, frame)
        self._store[problem_id] = frame
        return frame
