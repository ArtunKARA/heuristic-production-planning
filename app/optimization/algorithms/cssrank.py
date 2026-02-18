"""
TR: CSSRank (siralama tabanli Charged System Search) bu proje icin uyarlanmistir.

Kaynaklar / Atif:
1) Kaveh, A., Talatahari, S. (2010)
   A novel heuristic optimization method: charged system search.
   Acta Mechanica, 213, 267-289.
   DOI: https://doi.org/10.1007/s00707-009-0270-4
2) CSS iyilestirme/hybrid yaklasimlarina ornek:
   Shirgir, S., Hosseinzadeh, A.A., Jahan, A. (2024)
   Optimum design of steel trusses by charged system search
   trained by Nelder-Mead simplex algorithm.
   Expert Systems with Applications, 238, 121815.
   DOI: https://doi.org/10.1016/j.eswa.2023.121815

Not:
- Bu dosya, standart CSS makalesinin birebir kod kopyasi degildir.
- Rank tabanli yuk atama, hiz/konum guncelleme ve reset adimlari,
  bu projeye ozel pratik bir CSS uyarlamasi olarak tasarlanmistir.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Sequence, Tuple

from .base import Algorithm, AlgorithmContext, AlgorithmSpec
from .ga import _mutate_state
from .utils import is_better, select_initial_state, total_score


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


def _rank_key(eval_res: Dict[str, Any]) -> Tuple[int, float]:
    if eval_res is None:
        return (1, float("inf"))
    feasible = bool(eval_res.get("feasible", False))
    return (0 if feasible else 1, total_score(eval_res))


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
    particle_count = _positive_int(payload.get("css_particles", payload.get("population_size", 14)), 14)
    particle_count = max(3, particle_count)

    seed_base = _positive_int(payload.get("mutation_seed", 42), 42)
    rng = random.Random(seed_base + 1009)

    time_shift_max_hours = _bounded_float(payload.get("time_shift_max_hours", 8.0), 8.0, 0.0, 24.0)
    bucket_shift_max = _non_negative_int(payload.get("bucket_shift_max", 2), 2)
    qty_jitter_max_pct = _bounded_float(payload.get("qty_jitter_max_pct", 0.20), 0.20, 0.0, 1.0)
    machine_swap_max_rate = _bounded_float(payload.get("machine_swap_max_rate", 0.50), 0.50, 0.0, 1.0)
    mold_swap_max_rate = _bounded_float(payload.get("mold_swap_max_rate", 0.50), 0.50, 0.0, 1.0)

    top_ratio = _bounded_float(payload.get("css_top_ratio", 0.40), 0.40, 0.05, 1.0)
    damping = _bounded_float(payload.get("css_damping", 0.72), 0.72, 0.0, 0.99)
    accel = _bounded_float(payload.get("css_accel", 1.25), 1.25, 0.0, 5.0)
    elite_pull = _bounded_float(payload.get("css_elite_pull", 0.28), 0.28, 0.0, 2.0)
    reset_rate = _bounded_float(payload.get("css_reset_rate", 0.12), 0.12, 0.0, 0.8)
    noise_scale = _bounded_float(payload.get("css_noise_scale", 0.03), 0.03, 0.0, 0.3)

    dim = 7
    particles: List[List[float]] = [[rng.random() for _ in range(dim)] for _ in range(particle_count)]
    velocities: List[List[float]] = [
        [rng.uniform(-0.08, 0.08) for _ in range(dim)]
        for _ in range(particle_count)
    ]
    elite_vec = particles[0][:]
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
        evaluations: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        for idx in range(particle_count):
            particles[idx] = _bound_vector(particles[idx])
            cand_state, cand_eval = evaluate_position(particles[idx])
            evaluations.append((idx, cand_state, cand_eval))
            if is_better(cand_eval, elite_eval):
                elite_eval = cand_eval
                elite_state = cand_state
                elite_vec = particles[idx][:]

        ranked = sorted(evaluations, key=lambda item: _rank_key(item[2]))
        elite_idx = ranked[0][0]

        top_count = max(1, int(round(particle_count * top_ratio)))
        top_ranked = ranked[:top_count]
        charges: Dict[int, float] = {}
        for rank_pos, (idx, _, _) in enumerate(top_ranked):
            charges[idx] = (top_count - rank_pos) / top_count

        cooling = 1.0 - (iter_idx / max_iter)
        for idx in range(particle_count):
            if idx == elite_idx:
                continue
            cur = particles[idx]
            vel = velocities[idx]
            force = [0.0] * dim

            for other_idx, _, _ in top_ranked:
                if other_idx == idx:
                    continue
                other = particles[other_idx]
                diff = [other[d] - cur[d] for d in range(dim)]
                dist2 = sum(v * v for v in diff) + 1e-9
                charge = charges.get(other_idx, 0.0)
                coeff = charge / dist2
                for d in range(dim):
                    force[d] += coeff * diff[d]

            updated_vel: List[float] = []
            updated_pos: List[float] = []
            for d in range(dim):
                noise = noise_scale * cooling * rng.gauss(0.0, 1.0)
                v_next = (
                    damping * vel[d]
                    + accel * force[d]
                    + elite_pull * rng.random() * (elite_vec[d] - cur[d])
                    + noise
                )
                if v_next > 0.40:
                    v_next = 0.40
                if v_next < -0.40:
                    v_next = -0.40
                updated_vel.append(v_next)
                updated_pos.append(cur[d] + v_next)

            velocities[idx] = updated_vel
            particles[idx] = _bound_vector(updated_pos)

        reset_count = int(round(particle_count * reset_rate))
        if reset_count > 0:
            for idx, _, _ in ranked[-reset_count:]:
                if idx == elite_idx:
                    continue
                particles[idx] = [rng.random() for _ in range(dim)]
                velocities[idx] = [rng.uniform(-0.08, 0.08) for _ in range(dim)]

        ctx.record(elite_state, elite_eval, f"cssrank-{iter_idx}")

    return elite_state, elite_eval


ALGO = Algorithm(
    spec=AlgorithmSpec(
        code="cssrank",
        name="CSSRank",
        desc="Sıralama tabanlı yüklü sistem aramasıyla adayları rank/kuvvet dinamiğiyle günceller.",
        params={
            "max_iter": {"type": "int", "default": 30, "min": 1, "max": 300},
            "css_particles": {"type": "int", "default": 14, "min": 3, "max": 120},
            "css_top_ratio": {"type": "float", "default": 0.40, "min": 0.05, "max": 1.0},
            "css_damping": {"type": "float", "default": 0.72, "min": 0.0, "max": 0.99},
            "css_accel": {"type": "float", "default": 1.25, "min": 0.0, "max": 5.0},
            "css_elite_pull": {"type": "float", "default": 0.28, "min": 0.0, "max": 2.0},
            "css_reset_rate": {"type": "float", "default": 0.12, "min": 0.0, "max": 0.8},
            "css_noise_scale": {"type": "float", "default": 0.03, "min": 0.0, "max": 0.3},
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
