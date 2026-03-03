from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class AlgorithmSpec:
    code: str
    name: str
    params: Dict[str, Any]
    desc: Optional[str] = None


@dataclass
class Algorithm:
    spec: AlgorithmSpec
    planned_iterations: Callable[[Dict[str, Any]], int]
    run: Callable[["AlgorithmContext"], Tuple[Dict[str, Any], Dict[str, Any]]]


class AlgorithmContext:
    def __init__(
        self,
        *,
        problem: Dict[str, Any],
        scenario: Dict[str, Any],
        payload: Dict[str, Any],
        frame_state: Dict[str, Any],
        evaluate: Callable[[Dict[str, Any]], Dict[str, Any]],
        record: Callable[[Dict[str, Any], Dict[str, Any], str], None],
        build_greedy_plan: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        self.problem = problem
        self.scenario = scenario
        self.payload = payload
        self.frame_state = frame_state
        self._evaluate = evaluate
        self.eval_calls_total = 0
        self.record = record
        self.build_greedy_plan = build_greedy_plan

    def evaluate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self.eval_calls_total += 1
        return self._evaluate(state)
