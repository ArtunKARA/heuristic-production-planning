"""
TR: HMPA (Hybrid Marine Predators Algorithm) bu proje icin uyarlanmistir.

Kaynaklar / Atif:
1) Faramarzi, A., Heidarinejad, M., Mirjalili, S., Gandomi, A.H. (2020)
   Marine Predators Algorithm: A nature-inspired metaheuristic.
   Expert Systems with Applications, 152, 113377.
   DOI: https://doi.org/10.1016/j.eswa.2020.113377
2) Orijinal MPA referans kodu (MATLAB):
   https://github.com/afshinfaramarzi/Marine-Predators-Algorithm

Not:
- Bu dosya, yukaridaki kaynaklarin birebir kopyasi degildir.
- Uygulama, bu projedeki mevcut mutasyon/degerlendirme paternine
  uygun olacak sekilde hibritlestirilmis bir MPA varyantidir.
"""

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


def _non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


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


def _vector_key(vec: Sequence[float]) -> Tuple[int, ...]:
    return tuple(int(round(_clamp01(v) * 1_000_000)) for v in vec)


def _vector_seed(vec: Sequence[float], seed_base: int) -> int:
    fingerprint = 0
    for idx, val in enumerate(vec):
        fingerprint += (idx + 1) * int(round(_clamp01(val) * 100_000))
    return seed_base + fingerprint


def _levy_step(dim: int, rng: random.Random) -> List[float]:
    beta = 1.5
    sigma = (
        (
            math.gamma(1 + beta)
            * math.sin(math.pi * beta / 2)
            / (math.gamma((1 + beta) / 2) * beta * (2 ** ((beta - 1) / 2)))
        )
        ** (1 / beta)
    )
    out: List[float] = []
    for _ in range(dim):
        u = rng.gauss(0.0, sigma)
        v = rng.gauss(0.0, 1.0)
        out.append(u / (abs(v) ** (1 / beta) + 1e-12))
    return out


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
    predator_count = _positive_int(payload.get("hmpa_predators", payload.get("population_size", 12)), 12)
    predator_count = max(3, predator_count)

    seed_base = _positive_int(payload.get("mutation_seed", 42), 42)
    rng = random.Random(seed_base)

    time_shift_max_hours = _bounded_float(payload.get("time_shift_max_hours", 8.0), 8.0, 0.0, 24.0)
    bucket_shift_max = _non_negative_int(payload.get("bucket_shift_max", 2), 2)
    qty_jitter_max_pct = _bounded_float(payload.get("qty_jitter_max_pct", 0.20), 0.20, 0.0, 1.0)
    machine_swap_max_rate = _bounded_float(payload.get("machine_swap_max_rate", 0.50), 0.50, 0.0, 1.0)
    mold_swap_max_rate = _bounded_float(payload.get("mold_swap_max_rate", 0.50), 0.50, 0.0, 1.0)

    local_trials = _non_negative_int(payload.get("local_trials", 3), 3)
    local_radius = _bounded_float(payload.get("local_radius", 0.08), 0.08, 0.0, 0.5)
    local_search_every = _non_negative_int(payload.get("local_search_every", 4), 4)
    fads_rate = _bounded_float(payload.get("fads_rate", 0.15), 0.15, 0.0, 1.0)

    dim = 7
    predators: List[List[float]] = [[rng.random() for _ in range(dim)] for _ in range(predator_count)]
    elite_vec = predators[0][:]
    elite_state = base_state
    elite_eval = base_eval

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

    for iter_idx in range(1, max_iter + 1):
        # Elite update
        for i in range(predator_count):
            predators[i] = _bound_vector(predators[i])
            cand_state, cand_eval = evaluate_position(predators[i])
            if is_better(cand_eval, elite_eval):
                elite_eval = cand_eval
                elite_state = cand_state
                elite_vec = predators[i][:]

        progress = iter_idx / max_iter
        explore_scale = max(0.02, 0.35 * (1.0 - progress))
        exploit_scale = max(0.01, 0.20 * (1.0 - progress))

        if local_trials > 0 and local_search_every > 0 and (iter_idx % local_search_every == 0):
            for _ in range(local_trials):
                probe = [
                    elite_vec[d] + rng.uniform(-local_radius, local_radius)
                    for d in range(dim)
                ]
                probe = _bound_vector(probe)
                probe_state, probe_eval = evaluate_position(probe)
                if is_better(probe_eval, elite_eval):
                    elite_eval = probe_eval
                    elite_state = probe_state
                    elite_vec = probe[:]

        for i in range(predator_count):
            cur = predators[i]
            rand_pred = predators[rng.randrange(predator_count)]

            if progress < (1.0 / 3.0):
                updated = [
                    cur[d]
                    + rng.random() * (rand_pred[d] - rng.random() * cur[d])
                    + explore_scale * rng.gauss(0.0, 1.0)
                    for d in range(dim)
                ]
            elif progress < (2.0 / 3.0):
                r1 = rng.random()
                r2 = rng.random()
                updated = [
                    cur[d]
                    + r1 * (elite_vec[d] - cur[d])
                    + (1.0 - r1) * (rand_pred[d] - cur[d])
                    + explore_scale * r2 * (rng.random() - 0.5)
                    for d in range(dim)
                ]
            else:
                levy = _levy_step(dim, rng)
                updated = [
                    elite_vec[d]
                    + exploit_scale * levy[d] * (elite_vec[d] - cur[d])
                    + 0.15 * (elite_vec[d] - cur[d])
                    for d in range(dim)
                ]

            if fads_rate > 0.0 and rng.random() < fads_rate:
                j = rng.randrange(dim)
                updated[j] = rng.random()

            predators[i] = _bound_vector(updated)

        ctx.record(elite_state, elite_eval, f"hmpa-{iter_idx}")

    return elite_state, elite_eval


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="hmpa",
        name="HMPA",
        desc="Hybrid Marine Predators yaklaşımıyla fazlı keşif/sömürü ve lokal rafinman yapar.",
        params={
            "max_iter": {"type": "int", "default": 30, "min": 1, "max": 300},
            "hmpa_predators": {"type": "int", "default": 12, "min": 3, "max": 100},
            "local_trials": {"type": "int", "default": 3, "min": 0, "max": 20},
            "local_search_every": {"type": "int", "default": 4, "min": 0, "max": 30},
            "local_radius": {"type": "float", "default": 0.08, "min": 0.0, "max": 0.5},
            "fads_rate": {"type": "float", "default": 0.15, "min": 0.0, "max": 1.0},
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
