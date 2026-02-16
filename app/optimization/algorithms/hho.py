from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Sequence, Tuple

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .ga import _mutate_state
from .utils import is_better, select_initial_state


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bounded_float(value: Any, default: float, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < lower:
        return lower
    if parsed > upper:
        return upper
    return parsed


def _clamp01(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return float(value)


def _bound_vector(vec: Sequence[float]) -> List[float]:
    return [_clamp01(v) for v in vec]


def _mean_position(hawks: Sequence[Sequence[float]]) -> List[float]:
    if not hawks:
        return []
    dim = len(hawks[0])
    return [sum(h[d] for h in hawks) / len(hawks) for d in range(dim)]


def _levy(dim: int, rng: random.Random) -> List[float]:
    beta = 1.5
    sigma = (
        (
            math.gamma(1 + beta)
            * math.sin(math.pi * beta / 2)
            / (math.gamma((1 + beta) / 2) * beta * (2 ** ((beta - 1) / 2)))
        )
        ** (1 / beta)
    )
    steps: List[float] = []
    for _ in range(dim):
        u = rng.gauss(0.0, sigma)
        v = rng.gauss(0.0, 1.0)
        steps.append(u / (abs(v) ** (1 / beta) + 1e-12))
    return steps


def _vector_key(vec: Sequence[float]) -> Tuple[int, ...]:
    return tuple(int(round(_clamp01(v) * 1_000_000)) for v in vec)


def _vector_seed(vec: Sequence[float], seed_base: int) -> int:
    fp = 0
    for idx, val in enumerate(vec):
        fp += (idx + 1) * int(round(_clamp01(val) * 100_000))
    return seed_base + fp


def _vector_to_mutation(
    vec: Sequence[float],
    *,
    seed_base: int,
    time_shift_max_hours: float,
    bucket_shift_max: int,
    qty_jitter_max_pct: float,
    machine_swap_max_rate: float,
    mold_swap_max_rate: float,
) -> Tuple[float, Dict[str, Any]]:
    v = [_clamp01(x) for x in vec]

    time_shift_hours = ((2.0 * v[0]) - 1.0) * time_shift_max_hours
    if bucket_shift_max <= 0:
        bucket_shift = 0
    else:
        bucket_shift = int(round(((2.0 * v[1]) - 1.0) * bucket_shift_max))
    bucket_shift_rate = v[2]
    qty_jitter_pct = v[3] * qty_jitter_max_pct
    qty_jitter_rate = v[4]
    machine_swap_rate = v[5] * machine_swap_max_rate
    mold_swap_rate = v[6] * mold_swap_max_rate

    mutation_cfg = {
        "time_shift_hours": time_shift_hours,
        "bucket_shift": bucket_shift,
        "bucket_shift_rate": bucket_shift_rate,
        "qty_jitter_pct": qty_jitter_pct,
        "qty_jitter_rate": qty_jitter_rate,
        "machine_swap_rate": machine_swap_rate,
        "mold_swap_rate": mold_swap_rate,
        "mutation_seed": _vector_seed(v, seed_base),
    }
    return time_shift_hours, mutation_cfg


def planned_iterations(payload: Dict[str, Any]) -> int:
    max_iter = _positive_int(payload.get("max_iter", 30), 30)
    return 1 + max_iter


def run(ctx: AlgorithmContext) -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_state, base_eval, base_label = select_initial_state(ctx)
    ctx.record(base_state, base_eval, base_label)

    payload = ctx.payload
    max_iter = _positive_int(payload.get("max_iter", 30), 30)
    hawk_count = _positive_int(payload.get("hho_hawks", payload.get("population_size", 12)), 12)
    hawk_count = max(2, hawk_count)

    seed_base = _positive_int(payload.get("mutation_seed", 42), 42)
    rng = random.Random(seed_base)

    time_shift_max_hours = _bounded_float(payload.get("time_shift_max_hours", 8.0), 8.0, 0.0, 24.0)
    bucket_shift_max = _positive_int(payload.get("bucket_shift_max", 2), 2)
    qty_jitter_max_pct = _bounded_float(payload.get("qty_jitter_max_pct", 0.20), 0.20, 0.0, 1.0)
    machine_swap_max_rate = _bounded_float(payload.get("machine_swap_max_rate", 0.50), 0.50, 0.0, 1.0)
    mold_swap_max_rate = _bounded_float(payload.get("mold_swap_max_rate", 0.50), 0.50, 0.0, 1.0)

    dim = 7
    hawks: List[List[float]] = [[rng.random() for _ in range(dim)] for _ in range(hawk_count)]
    rabbit_location = hawks[0][:]
    rabbit_state = base_state
    rabbit_eval = base_eval

    eval_cache: Dict[Tuple[int, ...], Tuple[Dict[str, Any], Dict[str, Any]]] = {}

    def evaluate_position(vec: Sequence[float]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        bounded = _bound_vector(vec)
        key = _vector_key(bounded)
        cached = eval_cache.get(key)
        if cached is not None:
            return cached

        delta_hours, mutation_cfg = _vector_to_mutation(
            bounded,
            seed_base=seed_base,
            time_shift_max_hours=time_shift_max_hours,
            bucket_shift_max=bucket_shift_max,
            qty_jitter_max_pct=qty_jitter_max_pct,
            machine_swap_max_rate=machine_swap_max_rate,
            mold_swap_max_rate=mold_swap_max_rate,
        )
        mutated = _mutate_state(
            base_state,
            delta_hours=delta_hours,
            iter_no=1,
            mutation_cfg=mutation_cfg,
            problem=ctx.problem,
        )
        evaluation = ctx.evaluate(mutated)
        eval_cache[key] = (mutated, evaluation)
        return mutated, evaluation

    # Reference equations adapted from the official HHO MATLAB release:
    # https://github.com/aliasgharheidaricom/Harris-Hawks-Optimization-Algorithm-and-Applications
    for iter_idx in range(max_iter):
        hawk_evals: List[Dict[str, Any]] = [base_eval] * hawk_count

        for i in range(hawk_count):
            hawks[i] = _bound_vector(hawks[i])
            cand_state, cand_eval = evaluate_position(hawks[i])
            hawk_evals[i] = cand_eval
            if is_better(cand_eval, rabbit_eval):
                rabbit_eval = cand_eval
                rabbit_state = cand_state
                rabbit_location = hawks[i][:]

        e1 = 2.0 * (1.0 - (iter_idx / max_iter))
        mean_x = _mean_position(hawks)

        for i in range(hawk_count):
            cur = hawks[i]
            e0 = 2.0 * rng.random() - 1.0
            escaping_energy = e1 * e0

            if abs(escaping_energy) >= 1.0:
                q = rng.random()
                rand_hawk = hawks[rng.randrange(hawk_count)]
                if q < 0.5:
                    updated = [
                        rand_hawk[d] - rng.random() * abs(rand_hawk[d] - 2.0 * rng.random() * cur[d])
                        for d in range(dim)
                    ]
                else:
                    updated = [
                        (rabbit_location[d] - mean_x[d]) - rng.random() * rng.random()
                        for d in range(dim)
                    ]
                hawks[i] = _bound_vector(updated)
                continue

            r = rng.random()
            if r >= 0.5 and abs(escaping_energy) < 0.5:
                updated = [
                    rabbit_location[d] - escaping_energy * abs(rabbit_location[d] - cur[d])
                    for d in range(dim)
                ]
                hawks[i] = _bound_vector(updated)
                continue

            if r >= 0.5 and abs(escaping_energy) >= 0.5:
                jump_strength = 2.0 * (1.0 - rng.random())
                updated = [
                    (rabbit_location[d] - cur[d])
                    - escaping_energy * abs(jump_strength * rabbit_location[d] - cur[d])
                    for d in range(dim)
                ]
                hawks[i] = _bound_vector(updated)
                continue

            if r < 0.5 and abs(escaping_energy) >= 0.5:
                jump_strength = 2.0 * (1.0 - rng.random())
                x1 = _bound_vector([
                    rabbit_location[d] - escaping_energy * abs(jump_strength * rabbit_location[d] - cur[d])
                    for d in range(dim)
                ])
                _, x1_eval = evaluate_position(x1)
                if is_better(x1_eval, hawk_evals[i]):
                    hawks[i] = x1
                    hawk_evals[i] = x1_eval
                else:
                    levy = _levy(dim, rng)
                    x2 = _bound_vector([
                        rabbit_location[d]
                        - escaping_energy * abs(jump_strength * rabbit_location[d] - cur[d])
                        + rng.random() * levy[d]
                        for d in range(dim)
                    ])
                    _, x2_eval = evaluate_position(x2)
                    if is_better(x2_eval, hawk_evals[i]):
                        hawks[i] = x2
                        hawk_evals[i] = x2_eval
                continue

            jump_strength = 2.0 * (1.0 - rng.random())
            x1 = _bound_vector([
                rabbit_location[d] - escaping_energy * abs(jump_strength * rabbit_location[d] - mean_x[d])
                for d in range(dim)
            ])
            _, x1_eval = evaluate_position(x1)
            if is_better(x1_eval, hawk_evals[i]):
                hawks[i] = x1
            else:
                levy = _levy(dim, rng)
                x2 = _bound_vector([
                    rabbit_location[d]
                    - escaping_energy * abs(jump_strength * rabbit_location[d] - mean_x[d])
                    + rng.random() * levy[d]
                    for d in range(dim)
                ])
                _, x2_eval = evaluate_position(x2)
                if is_better(x2_eval, hawk_evals[i]):
                    hawks[i] = x2

        ctx.record(rabbit_state, rabbit_eval, f"hho-{iter_idx + 1}")

    return rabbit_state, rabbit_eval


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="hho",
        name="HHO",
        desc="Referans HHO formülleriyle (Keşif/Kuşatma/Rapid Dive) hiperparametre uzayında arama yapar.",
        params={
            "max_iter": {"type": "int", "default": 30, "min": 1, "max": 300},
            "hho_hawks": {"type": "int", "default": 12, "min": 2, "max": 100},
            "time_shift_max_hours": {"type": "float", "default": 8.0, "min": 0.0, "max": 24.0},
            "bucket_shift_max": {"type": "int", "default": 2, "min": 0, "max": 12},
            "qty_jitter_max_pct": {"type": "float", "default": 0.20, "min": 0.0, "max": 1.0},
            "machine_swap_max_rate": {"type": "float", "default": 0.50, "min": 0.0, "max": 1.0},
            "mold_swap_max_rate": {"type": "float", "default": 0.50, "min": 0.0, "max": 1.0},
            "mutation_seed": {"type": "int", "default": 42, "min": 0, "max": 999999},
        },
    ),
    planned_iterations=planned_iterations,
    run=run,
)


__all__ = ["ALGO"]
