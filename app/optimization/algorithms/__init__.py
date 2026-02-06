from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .greedy import ALGO as GREEDY
from .ga import ALGO as GA
from .tabu import ALGO as TABU
from .gatabu import ALGO as GATABU

ALGORITHMS: Dict[str, Algorithm] = {
    GREEDY.spec.code: GREEDY,
    GA.spec.code: GA,
    TABU.spec.code: TABU,
    GATABU.spec.code: GATABU,
}


def get_algorithm(code: str) -> Optional[Algorithm]:
    return ALGORITHMS.get(code)


def list_algorithm_specs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for algo in ALGORITHMS.values():
        spec: AlgorithmSpec = algo.spec
        out.append({
            "code": spec.code,
            "name": spec.name,
            "params": spec.params,
            "desc": spec.desc,
        })
    return out


__all__ = [
    "Algorithm",
    "AlgorithmContext",
    "AlgorithmSpec",
    "get_algorithm",
    "list_algorithm_specs",
]
