#!/usr/bin/env python3
"""
API-driven benchmark runner with persistent checkpoint/resume support.

Key features:
- Runs all selected algorithms in loops over N iterations and run count.
- Persists each trial under runs/<method>/<n_iter>/run_xxxx/...
- Writes progress checkpoint so interrupted runs can continue.
- Produces CSV tables and PNG plots (Word report intentionally omitted).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ITERATIONS = [10, 20, 30, 50, 100, 200]
DEFAULT_RUNS = 30
DEFAULT_METRIC = "total_score"
DEFAULT_EXCLUDE = {"greedy"}
DEFAULT_CONFIG_PATH = "Doc/benchmark_test_params.json"
DEFAULT_TABLE_METRICS = ["best", "mean", "worst", "std_dev"]
DEFAULT_PLOT_DPI = 600

FALLBACK_METHODS = [
    "ga",
    "tabu",
    "gatabu",
    "ga_tabu_inline",
    "ga_tabu_topk",
    "hho",
    "hmpa",
    "cssrank",
    "greedy",
]

RUN_RESULTS_FIELDS = [
    "job_key",
    "method",
    "n_iter",
    "run_id",
    "run",
    "seed",
    "metric",
    "metric_value",
    "feasible",
    "feasible_best",
    "best_total_score",
    "best_total_cost",
    "best_hard_total",
    "total_score",
    "total_cost",
    "hard_total",
    "iteration_count",
    "eval_calls_total",
    "eval_per_sec",
    "elapsed_sec",
    "status",
    "error",
    "started_at",
    "finished_at",
    "run_dir",
    "request_payload_json",
]

CONVERGENCE_FIELDS = [
    "job_key",
    "method",
    "n_iter",
    "run_id",
    "run",
    "seed",
    "outer_iter",
    "iteration_no",
    "label",
    "eval_calls_total",
    "metric",
    "metric_value",
    "best_so_far_metric_value",
    "current_total_score",
    "best_so_far_total_score",
    "current_total_cost",
    "best_so_far_total_cost",
    "current_hard_total",
    "best_so_far_hard_total",
    "total_score",
    "total_cost",
    "hard_total",
    "feasible",
]

PARAM_FIELDS = [
    "method",
    "n_iter",
    "runs",
    "metric",
    "ui_params_json",
    "common_params_json",
    "method_params_json",
]

SUMMARY_BY_METHOD_N_FIELDS = [
    "method",
    "n_iter",
    "metric",
    "runs_total",
    "runs_ok",
    "runs_failed",
    "best",
    "mean",
    "worst",
    "std_dev",
]

SUMMARY_BY_METHOD_FIELDS = [
    "method",
    "metric",
    "runs_ok",
    "runs_failed",
    "best",
    "mean",
    "worst",
    "std_dev",
]

FAIRNESS_BY_METHOD_N_FIELDS = [
    "method",
    "n_iter",
    "runs_ok",
    "feasible_rate",
    "median_best_hard_total",
    "mean_best_total_score",
    "std_best_total_score",
    "mean_best_total_cost",
    "std_best_total_cost",
    "mean_eval_calls_total",
    "std_eval_calls_total",
    "mean_elapsed_sec",
    "std_elapsed_sec",
]

FAIRNESS_BY_METHOD_FIELDS = [
    "method",
    "runs_ok",
    "feasible_rate",
    "median_best_hard_total",
    "mean_best_total_score",
    "std_best_total_score",
    "mean_best_total_cost",
    "std_best_total_cost",
    "mean_eval_calls_total",
    "std_eval_calls_total",
    "mean_elapsed_sec",
    "std_elapsed_sec",
]

FE_BUDGET_FIELDS = [
    "method",
    "n_iter",
    "fe_budget",
    "runs_reached_budget",
    "mean_best_so_far_total_score",
    "std_best_so_far_total_score",
    "mean_best_so_far_total_cost",
    "std_best_so_far_total_cost",
    "mean_best_so_far_hard_total",
    "median_best_so_far_hard_total",
    "feasible_rate_at_budget",
]


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_int_list(raw: str) -> List[int]:
    out: List[int] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if token:
            out.append(int(token))
    return out


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _unique_preserve_order(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        key = (item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _hard_total_from_eval(eval_res: Dict[str, Any]) -> float | None:
    hard_direct = _as_float(eval_res.get("hard_total"))
    if hard_direct is not None:
        return hard_direct
    cr = eval_res.get("constraint_results")
    if not isinstance(cr, dict):
        return None
    total = 0.0
    has_any = False
    for code, node in cr.items():
        if not str(code).startswith("HARD_"):
            continue
        if not isinstance(node, dict):
            continue
        val = _as_float(node.get("violation"))
        if val is None:
            continue
        total += val
        has_any = True
    return total if has_any else None


def _outer_iter_from_label(label: str, iteration_no: int) -> int:
    lab = (label or "").strip()
    if lab.lower() in {"", "greedy", "state"}:
        return 0
    m = re.match(r".*-(\d+)-(\d+)$", lab)
    if m:
        return int(m.group(1))
    m = re.match(r".*-(\d+)$", lab)
    if m:
        return int(m.group(1))
    return iteration_no if iteration_no > 0 else 0


def _first_not_none(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None:
            return value
    return default


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _slug(text: str) -> str:
    text = (text or "").strip().lower()
    safe = []
    for ch in text:
        if ch.isalnum() or ch in {"_", "-", "."}:
            safe.append(ch)
        else:
            safe.append("_")
    out = "".join(safe).strip("_")
    return out or "unnamed"


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False))
        f.write("\n")


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _ensure_csv_header(path: Path, fieldnames: List[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    _write_csv(path, [], fieldnames)


def _append_csv_rows(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    if not rows:
        return
    _ensure_csv_header(path, fieldnames)
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        for row in rows:
            writer.writerow(row)


def _load_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _build_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _request_json(
    method: str,
    base_url: str,
    path: str,
    payload: Dict[str, Any] | None,
    timeout_sec: float,
) -> Dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = Request(_build_url(base_url, path), data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode(resp.headers.get_content_charset("utf-8") or "utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} on {path}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Request failed on {path}: {exc.reason}") from exc

    try:
        data_out = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response on {path}: {body[:500]}") from exc
    if not isinstance(data_out, dict):
        raise RuntimeError(f"Unexpected response type on {path}: {type(data_out).__name__}")
    return data_out


def _post_json(base_url: str, path: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    return _request_json("POST", base_url, path, payload, timeout_sec)


def _get_json(base_url: str, path: str, timeout_sec: float) -> Dict[str, Any]:
    return _request_json("GET", base_url, path, None, timeout_sec)


def _extract_metric_from_iteration(iter_item: Dict[str, Any], metric: str) -> float | None:
    direct = _as_float(iter_item.get(metric))
    if direct is not None:
        return direct
    evaluation = iter_item.get("evaluation")
    if isinstance(evaluation, dict):
        return _as_float(evaluation.get(metric))
    return None


def _extract_best_metrics(result: Dict[str, Any], metric: str) -> Tuple[float | None, Dict[str, Any]]:
    best_eval = result.get("best_evaluation")
    if not isinstance(best_eval, dict):
        best_eval = {}
    metric_val = _as_float(best_eval.get(metric))
    if metric_val is None and metric != "total_score":
        metric_val = _as_float(best_eval.get("total_score"))
    if metric_val is None and metric != "total_cost":
        metric_val = _as_float(best_eval.get("total_cost"))
    if metric_val is None:
        iterations = result.get("iterations")
        if isinstance(iterations, list) and iterations:
            metric_val = _extract_metric_from_iteration(iterations[-1], metric)
    return metric_val, best_eval


def _discover_algorithms(base_url: str, frame_payload: Dict[str, Any], timeout_sec: float) -> List[str]:
    try:
        body = _post_json(
            base_url,
            "/sse/algorithms",
            {"problemData": frame_payload.get("problemData", {})},
            timeout_sec,
        )
        algos = body.get("algorithms")
        if isinstance(algos, list):
            codes = [a.get("code") for a in algos if isinstance(a, dict)]
            return [c for c in codes if isinstance(c, str) and c]
    except Exception:
        return [m for m in FALLBACK_METHODS]
    return [m for m in FALLBACK_METHODS]


def _select_methods(available_methods: List[str], include_methods: List[str] | None, exclude_methods: List[str]) -> List[str]:
    available = _unique_preserve_order(available_methods)
    exclude = set(_unique_preserve_order(exclude_methods))
    if include_methods:
        base = [m for m in _unique_preserve_order(include_methods) if m in available]
    else:
        base = [m for m in available]
    return [m for m in base if m not in exclude]


def _flatten_param_groups(node: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in node.items():
        if isinstance(value, dict):
            out.update(_flatten_param_groups(value))
        else:
            out[key] = value
    return out


def _job_key(method: str, n_iter: int, run_idx: int) -> str:
    return f"{method}|{n_iter}|{run_idx}"


def _job_key_from_row(row: Dict[str, Any]) -> str:
    if row.get("job_key"):
        return str(row["job_key"])
    run_idx = _as_int(_first_not_none(row.get("run"), row.get("run_id")), 0)
    return _job_key(str(row.get("method", "")), _as_int(row.get("n_iter"), 0), run_idx)


def _job_run_dir(root: Path, method: str, n_iter: int, run_idx: int) -> Path:
    return root / "runs" / _slug(method) / str(n_iter) / f"run_{run_idx:04d}"


def _build_plan_keys(methods: Sequence[str], iterations: Sequence[int], runs: int) -> set[str]:
    keys: set[str] = set()
    for method in methods:
        for n_iter in iterations:
            for run_idx in range(1, runs + 1):
                keys.add(_job_key(method, int(n_iter), run_idx))
    return keys


def _filter_rows_by_plan(rows: List[Dict[str, Any]], plan_keys: set[str]) -> List[Dict[str, Any]]:
    return [r for r in rows if _job_key_from_row(r) in plan_keys]


def _completed_job_keys(rows: List[Dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        status = str(row.get("status", "")).lower()
        if status in {"ok", "error"}:
            out.add(_job_key_from_row(row))
    return out


def _resolve_output_root(
    outdir: str,
    run_name: str,
    resume: bool,
    resume_dir: str,
) -> Path:
    base = Path(outdir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if resume_dir:
        return Path(resume_dir)
    if run_name:
        candidate = base / _slug(run_name)
    else:
        candidate = base / f"api_benchmark_{ts}"
    if candidate.exists() and any(candidate.iterdir()) and not resume and not resume_dir:
        return base / f"{candidate.name}_{ts}"
    return candidate


def _infer_last_completed_job(rows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    completed = [r for r in rows if str(r.get("status", "")).lower() in {"ok", "error"}]
    if not completed:
        return None
    completed_sorted = sorted(completed, key=lambda r: str(r.get("finished_at") or r.get("started_at") or ""))
    last = completed_sorted[-1]
    return {
        "job_key": _job_key_from_row(last),
        "method": str(last.get("method", "")),
        "n_iter": _as_int(last.get("n_iter"), 0),
        "run": _as_int(last.get("run"), 0),
    }


def _summarize_by_method_n(run_rows: List[Dict[str, Any]], metric: str, runs_per_n: int) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    grouped_failed: Dict[Tuple[str, int], int] = defaultdict(int)
    for row in run_rows:
        key = (str(row["method"]), _as_int(row["n_iter"], 0))
        if str(row.get("status", "")).lower() != "ok":
            grouped_failed[key] += 1
            continue
        value = _as_float(row.get("metric_value"))
        if value is None:
            grouped_failed[key] += 1
            continue
        grouped[key].append(value)

    out: List[Dict[str, Any]] = []
    for method, n_iter in sorted({*grouped.keys(), *grouped_failed.keys()}):
        vals = grouped.get((method, n_iter), [])
        failed = grouped_failed.get((method, n_iter), 0)
        row: Dict[str, Any] = {
            "method": method,
            "n_iter": n_iter,
            "metric": metric,
            "runs_total": runs_per_n,
            "runs_ok": len(vals),
            "runs_failed": failed,
            "best": "",
            "mean": "",
            "worst": "",
            "std_dev": "",
        }
        if vals:
            row["best"] = min(vals)
            row["mean"] = statistics.fmean(vals)
            row["worst"] = max(vals)
            row["std_dev"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        out.append(row)
    return out


def _summarize_by_method(run_rows: List[Dict[str, Any]], metric: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    failed: Dict[str, int] = defaultdict(int)
    for row in run_rows:
        method = str(row["method"])
        if str(row.get("status", "")).lower() != "ok":
            failed[method] += 1
            continue
        value = _as_float(row.get("metric_value"))
        if value is None:
            failed[method] += 1
            continue
        grouped[method].append(value)

    out: List[Dict[str, Any]] = []
    for method in sorted({*grouped.keys(), *failed.keys()}):
        vals = grouped.get(method, [])
        row: Dict[str, Any] = {
            "method": method,
            "metric": metric,
            "runs_ok": len(vals),
            "runs_failed": failed.get(method, 0),
            "best": "",
            "mean": "",
            "worst": "",
            "std_dev": "",
        }
        if vals:
            row["best"] = min(vals)
            row["mean"] = statistics.fmean(vals)
            row["worst"] = max(vals)
            row["std_dev"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        out.append(row)
    return out


def _wide_metric_table(summary_rows: List[Dict[str, Any]], metric_field: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    n_values = sorted({_as_int(r.get("n_iter"), 0) for r in summary_rows})
    methods = sorted({str(r.get("method", "")) for r in summary_rows})
    by_key: Dict[Tuple[str, int], Dict[str, Any]] = {
        (str(r.get("method", "")), _as_int(r.get("n_iter"), 0)): r for r in summary_rows
    }
    fieldnames = ["method"] + [f"n_{n}" for n in n_values]
    rows: List[Dict[str, Any]] = []
    for method in methods:
        row: Dict[str, Any] = {"method": method}
        for n in n_values:
            source = by_key.get((method, n), {})
            row[f"n_{n}"] = source.get(metric_field, "")
        rows.append(row)
    return fieldnames, rows


def _read_convergence_rows_filtered(path: Path, plan_keys: set[str]) -> List[Dict[str, Any]]:
    rows = _load_csv_rows(path)
    return [r for r in rows if _job_key_from_row(r) in plan_keys]


def _generate_plots(
    *,
    summary_rows: List[Dict[str, Any]],
    convergence_rows: List[Dict[str, Any]],
    run_rows: List[Dict[str, Any]],
    metric: str,
    plots_dir: Path,
    dpi: int,
) -> Dict[str, str]:
    files: Dict[str, str] = {}
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        warning_path = plots_dir / "PLOT_WARNING.txt"
        warning_path.parent.mkdir(parents=True, exist_ok=True)
        warning_path.write_text(
            "Matplotlib bulunamadi. Grafikler uretilmedi. Kurulum: pip install matplotlib\n",
            encoding="utf-8",
        )
        files["warning"] = str(warning_path)
        return files


    plots_dir.mkdir(parents=True, exist_ok=True)

    def _group_best_series(rows: List[Dict[str, Any]], value_field: str) -> Dict[str, List[List[Tuple[int, float]]]]:
        grouped: Dict[str, Dict[int, List[Tuple[int, float]]]] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            method = str(row.get("method", ""))
            run_no = _as_int(_first_not_none(row.get("run"), row.get("run_id")), 0)
            fe = _as_int(row.get("eval_calls_total"), 0)
            value = _as_float(row.get(value_field))
            if not method or run_no <= 0 or fe <= 0 or value is None:
                continue
            grouped[method][run_no].append((fe, value))

        out: Dict[str, List[List[Tuple[int, float]]]] = {}
        for method, by_run in grouped.items():
            out[method] = []
            for run_no in sorted(by_run.keys()):
                points = sorted(by_run[run_no], key=lambda p: p[0])
                if not points:
                    continue
                dedup: List[Tuple[int, float]] = []
                for fe, val in points:
                    if dedup and dedup[-1][0] == fe:
                        dedup[-1] = (fe, min(dedup[-1][1], val))
                    else:
                        dedup.append((fe, val))
                out[method].append(dedup)
        return out

    def _step_value_at_or_before(points: List[Tuple[int, float]], budget: int) -> float | None:
        current = None
        for fe, val in points:
            if fe > budget:
                break
            current = val
        return current

    def _aggregate_step_series(runs: List[List[Tuple[int, float]]]) -> Tuple[List[int], List[float], List[float]]:
        x_axis = sorted({fe for points in runs for fe, _ in points})
        means: List[float] = []
        stds: List[float] = []
        xs: List[int] = []
        for fe in x_axis:
            vals: List[float] = []
            for points in runs:
                v = _step_value_at_or_before(points, fe)
                if v is not None:
                    vals.append(v)
            if not vals:
                continue
            xs.append(fe)
            means.append(statistics.fmean(vals))
            stds.append(statistics.pstdev(vals) if len(vals) > 1 else 0.0)
        return xs, means, stds

    # Combined metric-vs-N plot.
    grouped_summary: Dict[str, List[Tuple[int, float, float, float]]] = defaultdict(list)
    for row in summary_rows:
        n_iter = _as_int(row.get("n_iter"), 0)
        mean_val = _as_float(row.get("mean"))
        best_val = _as_float(row.get("best"))
        worst_val = _as_float(row.get("worst"))
        if mean_val is None:
            continue
        grouped_summary[str(row.get("method", ""))].append((n_iter, mean_val, best_val or mean_val, worst_val or mean_val))

    if grouped_summary:
        fig, ax = plt.subplots(figsize=(9, 5))
        for method, vals in sorted(grouped_summary.items()):
            vals_sorted = sorted(vals, key=lambda x: x[0])
            x = [v[0] for v in vals_sorted]
            y = [v[1] for v in vals_sorted]
            ax.plot(x, y, linewidth=1.5, label=method)
        ax.set_title(f"All Methods: {metric} vs N")
        ax.set_xlabel("N iteration")
        ax.set_ylabel(metric)
        ax.grid(True, alpha=0.3)
        ax.legend()
        combined_path = plots_dir / "combined_metric_vs_n.png"
        fig.tight_layout()
        fig.savefig(combined_path, dpi=dpi)
        plt.close(fig)
        files["combined_metric_vs_n"] = str(combined_path)

        # Per-method metric-vs-N plots.
        for method, vals in sorted(grouped_summary.items()):
            vals_sorted = sorted(vals, key=lambda x: x[0])
            x = [v[0] for v in vals_sorted]
            y_mean = [v[1] for v in vals_sorted]
            y_best = [v[2] for v in vals_sorted]
            y_worst = [v[3] for v in vals_sorted]
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(x, y_mean, linewidth=1.5, label=f"{method} mean")
            ax.fill_between(x, y_best, y_worst, alpha=0.2, label="best-worst band")
            ax.set_title(f"{method}: {metric} vs N")
            ax.set_xlabel("N iteration")
            ax.set_ylabel(metric)
            ax.grid(True, alpha=0.3)
            ax.legend()
            p = plots_dir / f"{_slug(method)}_metric_vs_n.png"
            fig.tight_layout()
            fig.savefig(p, dpi=dpi)
            plt.close(fig)
            files[f"{method}_metric_vs_n"] = str(p)

    # Convergence at max N.
    if convergence_rows:
        n_ref = max(_as_int(r.get("n_iter"), 0) for r in convergence_rows)
        ref_rows = [r for r in convergence_rows if _as_int(r.get("n_iter"), 0) == n_ref]

        # -----------------------------
        # (0) FE-budget figures (best-so-far)
        # -----------------------------
        score_series = _group_best_series(ref_rows, "best_so_far_total_score")
        if score_series:
            fig, ax = plt.subplots(figsize=(9, 5))
            for method, runs in sorted(score_series.items()):
                x, y, y_std = _aggregate_step_series(runs)
                if not x:
                    continue
                y_lo = [m - s for m, s in zip(y, y_std)]
                y_hi = [m + s for m, s in zip(y, y_std)]
                ax.plot(x, y, linewidth=1.5, label=method)
                ax.fill_between(x, y_lo, y_hi, alpha=0.15)
            ax.set_title(f"Best-so-far total_score vs FE at N={n_ref}")
            ax.set_xlabel("eval_calls_total (FE)")
            ax.set_ylabel("best_so_far_total_score")
            ax.grid(True, alpha=0.3)
            ax.legend()
            p = plots_dir / f"combined_best_so_far_score_vs_fe_n{n_ref}.png"
            fig.tight_layout()
            fig.savefig(p, dpi=dpi)
            plt.close(fig)
            files[f"combined_best_so_far_score_vs_fe_n{n_ref}"] = str(p)

            # Final score distribution at a fixed FE budget (boxplot).
            min_common_fe_by_method: List[int] = []
            for method, runs in score_series.items():
                max_fe_per_run = [max(fe for fe, _ in points) for points in runs if points]
                if not max_fe_per_run:
                    continue
                min_common_fe_by_method.append(min(max_fe_per_run))
            global_common_fe = min(min_common_fe_by_method) if min_common_fe_by_method else 0
            if global_common_fe > 0:
                candidate_budgets = [1000, 5000, 10000]
                budget = max([b for b in candidate_budgets if b <= global_common_fe], default=global_common_fe)
                labels: List[str] = []
                data: List[List[float]] = []
                for method, runs in sorted(score_series.items()):
                    vals = []
                    for points in runs:
                        v = _step_value_at_or_before(points, budget)
                        if v is not None:
                            vals.append(v)
                    if vals:
                        labels.append(method)
                        data.append(vals)
                if data:
                    fig, ax = plt.subplots(figsize=(10, 5))
                    ax.boxplot(data, labels=labels, showmeans=True)
                    ax.set_title(f"Final best-so-far total_score distribution @FE={budget} (N={n_ref})")
                    ax.set_xlabel("method")
                    ax.set_ylabel("best_so_far_total_score")
                    ax.grid(True, axis="y", alpha=0.3)
                    p = plots_dir / f"boxplot_best_so_far_score_fe{budget}_n{n_ref}.png"
                    fig.tight_layout()
                    fig.savefig(p, dpi=dpi)
                    plt.close(fig)
                    files[f"boxplot_best_so_far_score_fe{budget}_n{n_ref}"] = str(p)

        hard_series = _group_best_series(ref_rows, "best_so_far_hard_total")
        if hard_series:
            fig, ax = plt.subplots(figsize=(9, 5))
            for method, runs in sorted(hard_series.items()):
                x, y, y_std = _aggregate_step_series(runs)
                if not x:
                    continue
                y_lo = [m - s for m, s in zip(y, y_std)]
                y_hi = [m + s for m, s in zip(y, y_std)]
                ax.plot(x, y, linewidth=1.5, label=method)
                ax.fill_between(x, y_lo, y_hi, alpha=0.15)
            ax.set_title(f"Best-so-far hard_total vs FE at N={n_ref}")
            ax.set_xlabel("eval_calls_total (FE)")
            ax.set_ylabel("best_so_far_hard_total")
            ax.grid(True, alpha=0.3)
            ax.legend()
            p = plots_dir / f"combined_best_so_far_hard_vs_fe_n{n_ref}.png"
            fig.tight_layout()
            fig.savefig(p, dpi=dpi)
            plt.close(fig)
            files[f"combined_best_so_far_hard_vs_fe_n{n_ref}"] = str(p)

        # Pareto (elapsed_sec vs final best_total_score) at max N.
        pareto_rows = [
            r
            for r in run_rows
            if str(r.get("status", "")).lower() == "ok" and _as_int(r.get("n_iter"), 0) == n_ref
        ]
        if pareto_rows:
            per_method: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: {"x": [], "y": []})
            for row in pareto_rows:
                method = str(row.get("method", ""))
                x = _as_float(row.get("elapsed_sec"))
                y = _as_float(_first_not_none(row.get("best_total_score"), row.get("total_score")))
                if not method or x is None or y is None:
                    continue
                per_method[method]["x"].append(x)
                per_method[method]["y"].append(y)
            if per_method:
                fig, ax = plt.subplots(figsize=(9, 5))
                for method, vals in sorted(per_method.items()):
                    x_vals = vals["x"]
                    y_vals = vals["y"]
                    if not x_vals or not y_vals:
                        continue
                    x_mean = statistics.fmean(x_vals)
                    y_mean = statistics.fmean(y_vals)
                    x_std = statistics.pstdev(x_vals) if len(x_vals) > 1 else 0.0
                    y_std = statistics.pstdev(y_vals) if len(y_vals) > 1 else 0.0
                    ax.errorbar(
                        x_mean,
                        y_mean,
                        xerr=x_std,
                        yerr=y_std,
                        fmt="o",
                        capsize=4,
                        label=method,
                    )
                ax.set_title(f"Pareto at N={n_ref} (elapsed_sec vs final best_total_score)")
                ax.set_xlabel("elapsed_sec")
                ax.set_ylabel("best_total_score")
                ax.grid(True, alpha=0.3)
                ax.legend()
                p = plots_dir / f"pareto_elapsed_vs_score_n{n_ref}.png"
                fig.tight_layout()
                fig.savefig(p, dpi=dpi)
                plt.close(fig)
                files[f"pareto_elapsed_vs_score_n{n_ref}"] = str(p)

        # -----------------------------
        # (A) Evaluation-level convergence (x = iteration_no)
        # -----------------------------
        grouped_conv: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        for row in ref_rows:
            method = str(row.get("method", ""))
            iter_no = _as_int(row.get("iteration_no"), -1)
            if iter_no <= 0:
                continue
            val = _as_float(row.get("metric_value"))
            if val is None:
                continue
            grouped_conv[method][iter_no].append(val)

        if grouped_conv:
            fig, ax = plt.subplots(figsize=(9, 5))
            for method, by_iter in sorted(grouped_conv.items()):
                x = sorted(by_iter.keys())
                y = [statistics.fmean(by_iter[i]) for i in x]
                ax.plot(x, y, linewidth=1.5, label=method)
            ax.set_title(f"Combined Convergence at N={n_ref}")
            ax.set_xlabel("iteration_no")
            ax.set_ylabel(metric)
            ax.grid(True, alpha=0.3)
            ax.legend()
            combined_conv_path = plots_dir / f"combined_convergence_n{n_ref}.png"
            fig.tight_layout()
            fig.savefig(combined_conv_path, dpi=dpi)
            plt.close(fig)
            files[f"combined_convergence_n{n_ref}"] = str(combined_conv_path)

            for method, by_iter in sorted(grouped_conv.items()):
                x = sorted(by_iter.keys())
                y = [statistics.fmean(by_iter[i]) for i in x]
                fig, ax = plt.subplots(figsize=(8, 5))
                ax.plot(x, y, linewidth=1.5, label=method)
                ax.set_title(f"{method} Convergence at N={n_ref}")
                ax.set_xlabel("iteration_no")
                ax.set_ylabel(metric)
                ax.grid(True, alpha=0.3)
                ax.legend()
                p = plots_dir / f"{_slug(method)}_convergence_n{n_ref}.png"
                fig.tight_layout()
                fig.savefig(p, dpi=dpi)
                plt.close(fig)
                files[f"{method}_convergence_n{n_ref}"] = str(p)


        # -----------------------------
        # (B) GA-primary outer-level convergence (x = GA generation)
        # -----------------------------

        _re_ga_gen = re.compile(r"^(?:ga|ga-inline|ga-topk)-(\d+)$")
        _re_tabu_inline = re.compile(r"^tabu-inline-(\d+)-(\d+)$")
        _re_tabu_topk = re.compile(r"^tabu-topk(\d+)-(\d+)$")   # topk-index, step
        _re_tabu_g_topk = re.compile(r"^tabu-g(\d+)-topk(\d+)-(\d+)$")  # ga-gen, topk-index, step
        _re_tabu_simple = re.compile(r"^tabu-(\d+)$")

        def _outer_iter_from_row(row: Dict[str, Any]) -> int:
            return _outer_iter_from_label(
                str(row.get("label", "") or "").strip(),
                _as_int(row.get("iteration_no"), -1),
            )

        def _extract_ga_gen(label: str) -> int | None:
            lab = (label or "").strip()
            if lab.lower() == "greedy":
                return 0
            m = _re_ga_gen.match(lab)
            if m:
                return int(m.group(1))
            m = _re_tabu_inline.match(lab)
            if m:
                return int(m.group(1))
            m = _re_tabu_g_topk.match(lab)
            if m:
                return int(m.group(1))
            return None


        last_ga_gen: Dict[Tuple[str, int], int] = {}
        for row in ref_rows:
            method = str(row.get("method", ""))
            run_no = _as_int(_first_not_none(row.get("run"), row.get("run_id")), 0)
            if run_no <= 0:
                continue
            label = str(row.get("label", "") or "")
            g = _extract_ga_gen(label)
            if g is None:
                continue
            last_ga_gen[(method, run_no)] = max(last_ga_gen.get((method, run_no), 0), g)

        best_by_run_gen: Dict[Tuple[str, int, int], float] = {}

        from itertools import groupby

        def _sort_key(r: Dict[str, Any]) -> Tuple[str, int, int]:
            return (
                str(r.get("method", "")),
                _as_int(_first_not_none(r.get("run"), r.get("run_id")), 0),
                _as_int(r.get("iteration_no"), 0),
            )

        ref_rows_sorted = sorted(ref_rows, key=_sort_key)

        for (method, run_no), rows_mr in groupby(
            ref_rows_sorted,
            key=lambda r: (
                str(r.get("method", "")),
                _as_int(_first_not_none(r.get("run"), r.get("run_id")), 0),
            ),
        ):
            if run_no <= 0:
                continue
            rows_mr = list(rows_mr)

            is_ga_based = any(_re_ga_gen.match(str(r.get("label","") or "").strip()) for r in rows_mr)

            current_gen: int | None = None
            fallback_last = last_ga_gen.get((method, run_no))

            for row in rows_mr:
                val = _as_float(row.get("metric_value"))
                if val is None:
                    continue
                label = str(row.get("label", "") or "").strip()

                g = _extract_ga_gen(label)
                if g is not None and g > 0:
                    current_gen = g

                if label.lower() == "greedy":
                    continue  

                if is_ga_based:
                    m_inline = _re_tabu_inline.match(label)
                    m_g_topk = _re_tabu_g_topk.match(label)
                    m_topk   = _re_tabu_topk.match(label)
                    m_simple = _re_tabu_simple.match(label)

                    if m_inline:
                        g_use = int(m_inline.group(1))
                    elif m_g_topk:
                        g_use = int(m_g_topk.group(1))
                    elif m_topk or m_simple:
                        g_use = current_gen if current_gen is not None else fallback_last
                    else:
                        g_use = current_gen

                    if g_use is None or g_use <= 0:
                        continue
                else:
                    g_use = _outer_iter_from_row(row)
                    if g_use is None or g_use <= 0:
                        continue

                key = (method, run_no, int(g_use))
                prev = best_by_run_gen.get(key)
                best_by_run_gen[key] = val if prev is None else min(prev, val)
        grouped_ga_primary: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        for (method, run_no, gen), best_val in best_by_run_gen.items():
            grouped_ga_primary[method][gen].append(best_val)

        if grouped_ga_primary:
            fig, ax = plt.subplots(figsize=(9, 5))
            for method, by_gen in sorted(grouped_ga_primary.items()):
                x = sorted(by_gen.keys())
                y = [statistics.fmean(by_gen[g]) for g in x]
                ax.plot(x, y, linewidth=1.5, label=method)
            ax.set_title(f"Combined GA-Primary Outer-Level Convergence at N={n_ref}")
            ax.set_xlabel("outer_iter (GA generation; tabu steps folded into parent GA gen)")
            ax.set_ylabel(metric)
            ax.grid(True, alpha=0.3)
            ax.legend()
            combined_ga_primary_path = plots_dir / f"combined_outer_ga_primary_n{n_ref}.png"
            fig.tight_layout()
            fig.savefig(combined_ga_primary_path, dpi=dpi)
            plt.close(fig)
            files[f"combined_outer_ga_primary_n{n_ref}"] = str(combined_ga_primary_path)

            for method, by_gen in sorted(grouped_ga_primary.items()):
                x = sorted(by_gen.keys())
                y = [statistics.fmean(by_gen[g]) for g in x]
                fig, ax = plt.subplots(figsize=(8, 5))
                ax.plot(x, y, linewidth=1.5, label=method)
                ax.set_title(f"{method} GA-Primary Outer-Level Convergence at N={n_ref}")
                ax.set_xlabel("outer_iter (GA generation for GA-based methods)")
                ax.set_ylabel(metric)
                ax.grid(True, alpha=0.3)
                ax.legend()
                p = plots_dir / f"{_slug(method)}_outer_ga_primary_n{n_ref}.png"
                fig.tight_layout()
                fig.savefig(p, dpi=dpi)
                plt.close(fig)
                files[f"{method}_outer_ga_primary_n{n_ref}"] = str(p)

    return files



def _refresh_tables(
    *,
    run_rows: List[Dict[str, Any]],
    metric: str,
    runs_per_n: int,
    csv_dir: Path,
    table_metrics: Sequence[str],
) -> Dict[str, str]:
    files: Dict[str, str] = {}
    summary_by_n = _summarize_by_method_n(run_rows, metric, runs_per_n)
    summary_by_method = _summarize_by_method(run_rows, metric)

    summary_n_path = csv_dir / "summary_by_method_n.csv"
    summary_m_path = csv_dir / "summary_by_method.csv"
    _write_csv(summary_n_path, summary_by_n, SUMMARY_BY_METHOD_N_FIELDS)
    _write_csv(summary_m_path, summary_by_method, SUMMARY_BY_METHOD_FIELDS)
    files["summary_by_method_n"] = str(summary_n_path)
    files["summary_by_method"] = str(summary_m_path)

    for metric_field in table_metrics:
        if metric_field not in {"best", "mean", "worst", "std_dev"}:
            continue
        fn, rows = _wide_metric_table(summary_by_n, metric_field)
        path = csv_dir / f"table_{metric_field}_matrix.csv"
        _write_csv(path, rows, fn)
        files[f"table_{metric_field}_matrix"] = str(path)

    return files


def _quality_feasibility_efficiency_rows(run_rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        if str(row.get("status", "")).lower() != "ok":
            continue
        method = str(row.get("method", ""))
        n_iter = _as_int(row.get("n_iter"), 0)
        grouped[(method, n_iter)].append(row)

    def _build_summary(rows_in_group: List[Dict[str, Any]]) -> Dict[str, Any]:
        score_vals: List[float] = []
        cost_vals: List[float] = []
        hard_vals: List[float] = []
        eval_vals: List[float] = []
        elapsed_vals: List[float] = []
        feasible_hits = 0

        for row in rows_in_group:
            score = _as_float(_first_not_none(row.get("best_total_score"), row.get("total_score")))
            cost = _as_float(_first_not_none(row.get("best_total_cost"), row.get("total_cost")))
            hard = _as_float(_first_not_none(row.get("best_hard_total"), row.get("hard_total")))
            eval_calls = _as_float(row.get("eval_calls_total"))
            elapsed = _as_float(row.get("elapsed_sec"))

            if score is not None:
                score_vals.append(score)
            if cost is not None:
                cost_vals.append(cost)
            if hard is not None:
                hard_vals.append(hard)
            if eval_calls is not None:
                eval_vals.append(eval_calls)
            if elapsed is not None:
                elapsed_vals.append(elapsed)

            feasible_best = _as_bool(row.get("feasible_best"))
            if feasible_best is None:
                feasible_best = _as_bool(row.get("feasible"))
            if feasible_best is None and hard is not None:
                feasible_best = hard == 0.0
            if feasible_best:
                feasible_hits += 1

        runs_ok = len(rows_in_group)
        return {
            "runs_ok": runs_ok,
            "feasible_rate": (feasible_hits / runs_ok) if runs_ok else "",
            "median_best_hard_total": statistics.median(hard_vals) if hard_vals else "",
            "mean_best_total_score": statistics.fmean(score_vals) if score_vals else "",
            "std_best_total_score": statistics.pstdev(score_vals) if len(score_vals) > 1 else (0.0 if score_vals else ""),
            "mean_best_total_cost": statistics.fmean(cost_vals) if cost_vals else "",
            "std_best_total_cost": statistics.pstdev(cost_vals) if len(cost_vals) > 1 else (0.0 if cost_vals else ""),
            "mean_eval_calls_total": statistics.fmean(eval_vals) if eval_vals else "",
            "std_eval_calls_total": statistics.pstdev(eval_vals) if len(eval_vals) > 1 else (0.0 if eval_vals else ""),
            "mean_elapsed_sec": statistics.fmean(elapsed_vals) if elapsed_vals else "",
            "std_elapsed_sec": statistics.pstdev(elapsed_vals) if len(elapsed_vals) > 1 else (0.0 if elapsed_vals else ""),
        }

    by_method_n: List[Dict[str, Any]] = []
    for (method, n_iter), rows_in_group in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        summary = _build_summary(rows_in_group)
        by_method_n.append(
            {
                "method": method,
                "n_iter": n_iter,
                **summary,
            }
        )

    grouped_method: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in by_method_n:
        grouped_method[str(row.get("method", ""))].append(row)

    by_method: List[Dict[str, Any]] = []
    for method in sorted(grouped_method.keys()):
        rows_for_method = []
        for row in run_rows:
            if str(row.get("status", "")).lower() != "ok":
                continue
            if str(row.get("method", "")) != method:
                continue
            rows_for_method.append(row)
        summary = _build_summary(rows_for_method)
        by_method.append({"method": method, **summary})

    return by_method_n, by_method


def _fe_budget_rows(convergence_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, int, int], List[Tuple[int, float | None, float | None, float | None]]] = defaultdict(list)
    for row in convergence_rows:
        method = str(row.get("method", ""))
        n_iter = _as_int(row.get("n_iter"), 0)
        run_no = _as_int(_first_not_none(row.get("run"), row.get("run_id")), 0)
        fe = _as_int(row.get("eval_calls_total"), 0)
        if not method or n_iter <= 0 or run_no <= 0 or fe <= 0:
            continue
        score = _as_float(_first_not_none(row.get("best_so_far_total_score"), row.get("current_total_score"), row.get("total_score")))
        cost = _as_float(_first_not_none(row.get("best_so_far_total_cost"), row.get("current_total_cost"), row.get("total_cost")))
        hard = _as_float(_first_not_none(row.get("best_so_far_hard_total"), row.get("current_hard_total"), row.get("hard_total")))
        grouped[(method, n_iter, run_no)].append((fe, score, cost, hard))

    dedup_series: Dict[Tuple[str, int, int], List[Tuple[int, float | None, float | None, float | None]]] = {}
    for key, points in grouped.items():
        ordered = sorted(points, key=lambda x: x[0])
        dedup: List[Tuple[int, float | None, float | None, float | None]] = []
        for fe, score, cost, hard in ordered:
            if dedup and dedup[-1][0] == fe:
                prev_fe, prev_score, prev_cost, prev_hard = dedup[-1]
                dedup[-1] = (
                    prev_fe,
                    min(prev_score, score) if (prev_score is not None and score is not None) else _first_not_none(score, prev_score),
                    min(prev_cost, cost) if (prev_cost is not None and cost is not None) else _first_not_none(cost, prev_cost),
                    min(prev_hard, hard) if (prev_hard is not None and hard is not None) else _first_not_none(hard, prev_hard),
                )
            else:
                dedup.append((fe, score, cost, hard))
        dedup_series[key] = dedup

    methods_by_n: Dict[int, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    for (method, n_iter, _run_no), points in dedup_series.items():
        if points:
            methods_by_n[n_iter][method].append(points[-1][0])

    rows_out: List[Dict[str, Any]] = []
    candidate_budgets = [500, 1000, 5000, 10000]

    for n_iter in sorted(methods_by_n.keys()):
        method_caps: List[int] = []
        for method in methods_by_n[n_iter]:
            run_caps = methods_by_n[n_iter][method]
            if run_caps:
                method_caps.append(min(run_caps))
        if not method_caps:
            continue
        global_common = min(method_caps)
        budgets = [b for b in candidate_budgets if b <= global_common]
        if global_common > 0 and global_common not in budgets:
            budgets.append(global_common)
        budgets = sorted(set(budgets))
        if not budgets:
            continue

        for method in sorted(methods_by_n[n_iter].keys()):
            series_for_method = [
                points
                for (m, n, _r), points in dedup_series.items()
                if m == method and n == n_iter and points
            ]
            if not series_for_method:
                continue

            for budget in budgets:
                score_vals: List[float] = []
                cost_vals: List[float] = []
                hard_vals: List[float] = []
                feasible_hits = 0
                runs_reached = 0

                for points in series_for_method:
                    last_score = None
                    last_cost = None
                    last_hard = None
                    for fe, score, cost, hard in points:
                        if fe > budget:
                            break
                        last_score = score
                        last_cost = cost
                        last_hard = hard
                    if last_score is None and last_cost is None and last_hard is None:
                        continue
                    runs_reached += 1
                    if last_score is not None:
                        score_vals.append(last_score)
                    if last_cost is not None:
                        cost_vals.append(last_cost)
                    if last_hard is not None:
                        hard_vals.append(last_hard)
                        if last_hard == 0.0:
                            feasible_hits += 1

                if runs_reached <= 0:
                    continue

                rows_out.append(
                    {
                        "method": method,
                        "n_iter": n_iter,
                        "fe_budget": budget,
                        "runs_reached_budget": runs_reached,
                        "mean_best_so_far_total_score": statistics.fmean(score_vals) if score_vals else "",
                        "std_best_so_far_total_score": (
                            statistics.pstdev(score_vals) if len(score_vals) > 1 else (0.0 if score_vals else "")
                        ),
                        "mean_best_so_far_total_cost": statistics.fmean(cost_vals) if cost_vals else "",
                        "std_best_so_far_total_cost": (
                            statistics.pstdev(cost_vals) if len(cost_vals) > 1 else (0.0 if cost_vals else "")
                        ),
                        "mean_best_so_far_hard_total": statistics.fmean(hard_vals) if hard_vals else "",
                        "median_best_so_far_hard_total": statistics.median(hard_vals) if hard_vals else "",
                        "feasible_rate_at_budget": (feasible_hits / runs_reached) if runs_reached else "",
                    }
                )

    return rows_out


def _refresh_fairness_tables(
    *,
    run_rows: List[Dict[str, Any]],
    convergence_rows: List[Dict[str, Any]],
    csv_dir: Path,
) -> Dict[str, str]:
    files: Dict[str, str] = {}

    fair_n, fair_m = _quality_feasibility_efficiency_rows(run_rows)
    fair_n_path = csv_dir / "fairness_by_method_n.csv"
    fair_m_path = csv_dir / "fairness_by_method.csv"
    _write_csv(fair_n_path, fair_n, FAIRNESS_BY_METHOD_N_FIELDS)
    _write_csv(fair_m_path, fair_m, FAIRNESS_BY_METHOD_FIELDS)
    files["fairness_by_method_n"] = str(fair_n_path)
    files["fairness_by_method"] = str(fair_m_path)

    fe_budget_rows = _fe_budget_rows(convergence_rows)
    fe_budget_path = csv_dir / "fe_budget_summary.csv"
    _write_csv(fe_budget_path, fe_budget_rows, FE_BUDGET_FIELDS)
    files["fe_budget_summary"] = str(fe_budget_path)

    return files


def _load_ui_config(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config file must be a JSON object.")
    return data


def _merge_runtime_settings(args: argparse.Namespace, ui_cfg: Dict[str, Any]) -> Dict[str, Any]:
    api_cfg = _as_dict(ui_cfg.get("api"))
    io_cfg = _as_dict(ui_cfg.get("input_output"))
    plan_cfg = _as_dict(ui_cfg.get("benchmark_plan"))
    selection_cfg = _as_dict(ui_cfg.get("algorithm_selection"))
    params_cfg = _as_dict(ui_cfg.get("parameters"))

    iterations_raw = _first_not_none(
        plan_cfg.get("iteration_list"),
        ui_cfg.get("n_iterations"),
        ui_cfg.get("iterations"),
        args.iterations,
    )
    if isinstance(iterations_raw, str):
        iterations = _parse_int_list(iterations_raw)
    elif isinstance(iterations_raw, list):
        iterations = [int(v) for v in iterations_raw]
    else:
        raise ValueError("iterations must be string/list.")
    if not iterations:
        raise ValueError("iterations list cannot be empty.")

    runs = int(
        _first_not_none(
            plan_cfg.get("runs_per_iteration"),
            ui_cfg.get("runs_per_n"),
            ui_cfg.get("runs"),
            args.runs,
        )
    )
    if runs <= 0:
        raise ValueError("runs must be > 0.")

    metric = str(
        _first_not_none(
            plan_cfg.get("primary_metric"),
            ui_cfg.get("metric"),
            ui_cfg.get("score_metric"),
            args.metric,
        )
    )

    include_raw = _first_not_none(
        selection_cfg.get("include_methods"),
        ui_cfg.get("methods"),
        ui_cfg.get("include_methods"),
        ui_cfg.get("algorithms"),
        args.methods,
    )
    if isinstance(include_raw, str):
        include_methods = [m.strip() for m in include_raw.split(",") if m.strip()]
    elif isinstance(include_raw, list):
        include_methods = [str(v).strip() for v in include_raw if str(v).strip()]
    elif include_raw is None:
        include_methods = []
    else:
        raise ValueError("include_methods must be string/list.")

    exclude_raw = _first_not_none(
        selection_cfg.get("exclude_methods"),
        ui_cfg.get("exclude_methods"),
        ui_cfg.get("excluded_algorithms"),
        args.exclude_methods,
    )
    if isinstance(exclude_raw, str):
        exclude_methods = [m.strip() for m in exclude_raw.split(",") if m.strip()]
    elif isinstance(exclude_raw, list):
        exclude_methods = [str(v).strip() for v in exclude_raw if str(v).strip()]
    else:
        raise ValueError("exclude_methods must be string/list.")
    if not exclude_methods:
        exclude_methods = list(DEFAULT_EXCLUDE)

    ui_params_raw = _first_not_none(
        params_cfg.get("ui_global_parameters"),
        ui_cfg.get("ui_params"),
        ui_cfg.get("ui_algorithm_params"),
        ui_cfg.get("algorithm_payload_params"),
        {},
    )
    if not isinstance(ui_params_raw, dict):
        raise ValueError("ui params must be object.")
    ui_params = _flatten_param_groups(ui_params_raw)

    common_params = _first_not_none(
        params_cfg.get("common_request_overrides"),
        ui_cfg.get("common_params"),
        ui_cfg.get("global_params"),
        ui_cfg.get("payload_overrides"),
        {},
    )
    if not isinstance(common_params, dict):
        raise ValueError("common params must be object.")

    method_params = _first_not_none(
        params_cfg.get("method_specific_overrides"),
        ui_cfg.get("method_params"),
        ui_cfg.get("algorithm_params"),
        {},
    )
    if not isinstance(method_params, dict):
        raise ValueError("method params must be object.")

    settings = {
        "api_base_url": str(
            _first_not_none(
                api_cfg.get("base_url"),
                ui_cfg.get("api_base_url"),
                ui_cfg.get("base_url"),
                args.api_base_url,
            )
        ),
        "input": str(
            _first_not_none(
                io_cfg.get("input_file"),
                ui_cfg.get("input"),
                ui_cfg.get("input_file"),
                args.input,
            )
        ),
        "outdir": str(
            _first_not_none(
                io_cfg.get("output_dir"),
                ui_cfg.get("outdir"),
                ui_cfg.get("output_dir"),
                args.outdir,
            )
        ),
        "iterations": iterations,
        "runs": runs,
        "metric": metric,
        "include_methods": include_methods,
        "exclude_methods": exclude_methods,
        "ui_params": ui_params,
        "common_params": common_params,
        "method_params": method_params,
        "timeout_sec": float(
            _first_not_none(
                api_cfg.get("timeout_seconds"),
                ui_cfg.get("timeout"),
                args.timeout,
            )
        ),
        "seed_offset": int(
            _first_not_none(
                plan_cfg.get("seed_offset"),
                ui_cfg.get("seed_offset"),
                args.seed_offset,
            )
        ),
        "run_name": str(
            _first_not_none(
                plan_cfg.get("run_name"),
                ui_cfg.get("run_name"),
                args.run_name,
            )
            or ""
        ),
        "resume": _parse_bool(
            _first_not_none(
                plan_cfg.get("resume"),
                ui_cfg.get("resume"),
                args.resume,
            ),
            default=False,
        ),
        "resume_dir": str(
            _first_not_none(
                ui_cfg.get("resume_dir"),
                args.resume_dir,
            )
            or ""
        ),
        "generate_plots": _parse_bool(
            _first_not_none(
                plan_cfg.get("generate_plots"),
                ui_cfg.get("generate_plots"),
                args.generate_plots,
            ),
            default=True,
        ),
        "plot_dpi": int(
            _first_not_none(
                plan_cfg.get("plot_dpi"),
                ui_cfg.get("plot_dpi"),
                args.plot_dpi,
            )
        ),
        "table_metrics": _first_not_none(
            plan_cfg.get("table_metrics"),
            ui_cfg.get("table_metrics"),
            DEFAULT_TABLE_METRICS,
        ),
    }
    if not isinstance(settings["table_metrics"], list):
        settings["table_metrics"] = list(DEFAULT_TABLE_METRICS)
    settings["table_metrics"] = [str(v) for v in settings["table_metrics"]]
    return settings


def _ensure_frame_id(
    *,
    api_base_url: str,
    timeout_sec: float,
    frame_payload: Dict[str, Any],
    existing_frame_id: str,
) -> str:
    if existing_frame_id:
        try:
            _get_json(api_base_url, f"/frame/{existing_frame_id}", timeout_sec)
            return existing_frame_id
        except Exception:
            pass
    create_resp = _post_json(api_base_url, "/frame", frame_payload, timeout_sec)
    frame_id = create_resp.get("id")
    if not isinstance(frame_id, str) or not frame_id:
        raise RuntimeError("Could not get frame id from /frame response.")
    return frame_id


def run_api_benchmark(settings: Dict[str, Any]) -> Dict[str, Any]:
    api_base_url = settings["api_base_url"]
    input_path = Path(settings["input"])
    iterations: List[int] = settings["iterations"]
    runs_per_n: int = settings["runs"]
    metric: str = settings["metric"]
    include_methods: List[str] = settings["include_methods"]
    exclude_methods: List[str] = settings["exclude_methods"]
    ui_params: Dict[str, Any] = settings["ui_params"]
    common_params: Dict[str, Any] = settings["common_params"]
    method_params: Dict[str, Dict[str, Any]] = settings["method_params"]
    timeout_sec: float = settings["timeout_sec"]
    seed_offset: int = settings["seed_offset"]
    generate_plots: bool = settings["generate_plots"]
    plot_dpi: int = max(72, int(settings["plot_dpi"]))
    table_metrics: List[str] = settings["table_metrics"]

    frame_payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(frame_payload, dict):
        raise ValueError("Input file must contain a JSON object.")

    output_root = _resolve_output_root(
        settings["outdir"],
        settings["run_name"],
        settings["resume"],
        settings["resume_dir"],
    )
    csv_dir = output_root / "csv"
    state_dir = output_root / "state"
    plots_dir = output_root / "plots"
    runs_root = output_root / "runs"
    for d in (csv_dir, state_dir, plots_dir, runs_root):
        d.mkdir(parents=True, exist_ok=True)

    run_csv = csv_dir / "run_results.csv"
    conv_csv = csv_dir / "convergence_history.csv"
    params_csv = csv_dir / "parameter_list.csv"
    summary_csv = csv_dir / "summary_by_method_n.csv"
    summary_method_csv = csv_dir / "summary_by_method.csv"
    checkpoint_path = state_dir / "checkpoint.json"
    event_log_path = state_dir / "events.ndjson"

    _ensure_csv_header(run_csv, RUN_RESULTS_FIELDS)
    _ensure_csv_header(conv_csv, CONVERGENCE_FIELDS)

    available_methods = _discover_algorithms(api_base_url, frame_payload, timeout_sec)
    methods = _select_methods(available_methods, include_methods, exclude_methods)
    if not methods:
        raise RuntimeError(
            f"No strategy left to run. available={available_methods}, include={include_methods}, exclude={exclude_methods}"
        )

    plan_keys = _build_plan_keys(methods, iterations, runs_per_n)
    total_jobs = len(plan_keys)

    # Load existing rows for resume.
    run_rows_all = _load_csv_rows(run_csv)
    run_rows = _filter_rows_by_plan(run_rows_all, plan_keys)
    completed_keys = _completed_job_keys(run_rows)
    last_completed_job = _infer_last_completed_job(run_rows)

    # Persist parameter table (current plan snapshot).
    params_rows: List[Dict[str, Any]] = []
    for method in methods:
        for n_iter in iterations:
            params_rows.append(
                {
                    "method": method,
                    "n_iter": n_iter,
                    "runs": runs_per_n,
                    "metric": metric,
                    "ui_params_json": _safe_json_dumps(ui_params),
                    "common_params_json": _safe_json_dumps(common_params),
                    "method_params_json": _safe_json_dumps(method_params.get(method, {})),
                }
            )
    _write_csv(params_csv, params_rows, PARAM_FIELDS)

    frame_id = ""
    existing_checkpoint: Dict[str, Any] = {}
    if checkpoint_path.exists():
        try:
            cp_old = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if isinstance(cp_old, dict):
                existing_checkpoint = cp_old
                frame_id = str(cp_old.get("frame_id") or "")
        except Exception:
            frame_id = ""
    frame_id = _ensure_frame_id(
        api_base_url=api_base_url,
        timeout_sec=timeout_sec,
        frame_payload=frame_payload,
        existing_frame_id=frame_id,
    )

    started = time.perf_counter()
    started_at = str(existing_checkpoint.get("started_at") or _now_iso())
    checkpoint = {
        "status": "running",
        "started_at": started_at,
        "updated_at": _now_iso(),
        "output_root": str(output_root),
        "frame_id": frame_id,
        "metric": metric,
        "methods": methods,
        "iterations": iterations,
        "runs_per_iteration": runs_per_n,
        "total_jobs": total_jobs,
        "completed_jobs": len(completed_keys),
        "remaining_jobs": max(total_jobs - len(completed_keys), 0),
        "current_job": None,
        "last_completed_job": last_completed_job,
    }
    _atomic_write_json(checkpoint_path, checkpoint)
    _atomic_write_json(state_dir / "resolved_settings.json", settings)

    done_counter = len(completed_keys)
    jobs: List[Tuple[str, int, int]] = []
    for method in methods:
        for n_iter in iterations:
            for run_idx in range(1, runs_per_n + 1):
                jobs.append((method, int(n_iter), run_idx))

    for method, n_iter, run_idx in jobs:
        job_key = _job_key(method, n_iter, run_idx)
        if job_key in completed_keys:
            continue

        done_counter += 1
        seed = seed_offset + run_idx
        run_dir = _job_run_dir(output_root, method, n_iter, run_idx)
        run_dir.mkdir(parents=True, exist_ok=True)

        optimize_payload: Dict[str, Any] = {"strategy": method}
        optimize_payload.update(ui_params)
        optimize_payload.update(common_params)
        method_extra = method_params.get(method, {}) or {}
        if not isinstance(method_extra, dict):
            raise ValueError(f"method_params[{method}] must be an object.")
        optimize_payload.update(method_extra)
        # Loop values always override.
        optimize_payload["max_iter"] = n_iter
        optimize_payload["mutation_seed"] = seed

        if method == "tabu":
            optimize_payload.pop("tabu_iter", None)
        request_path = run_dir / "request.json"
        _atomic_write_json(
            request_path,
            {
                "job_key": job_key,
                "method": method,
                "n_iter": n_iter,
                "run": run_idx,
                "seed": seed,
                "payload": optimize_payload,
            },
        )

        checkpoint["current_job"] = {"job_key": job_key, "method": method, "n_iter": n_iter, "run": run_idx}
        checkpoint["updated_at"] = _now_iso()
        _atomic_write_json(checkpoint_path, checkpoint)

        started_run_at = _now_iso()
        t0 = time.perf_counter()
        status = "ok"
        error_msg = ""
        response: Dict[str, Any] = {}
        try:
            response = _post_json(api_base_url, f"/frame/{frame_id}/optimize", optimize_payload, timeout_sec)
        except Exception as exc:
            msg = str(exc)
            if "HTTP 404" in msg and "Frame not found" in msg:
                frame_id = _ensure_frame_id(
                    api_base_url=api_base_url,
                    timeout_sec=timeout_sec,
                    frame_payload=frame_payload,
                    existing_frame_id="",
                )
                checkpoint["frame_id"] = frame_id
                _atomic_write_json(checkpoint_path, checkpoint)
                try:
                    response = _post_json(api_base_url, f"/frame/{frame_id}/optimize", optimize_payload, timeout_sec)
                except Exception as exc2:
                    status = "error"
                    error_msg = str(exc2)
            else:
                status = "error"
                error_msg = msg

        elapsed_sec = round(time.perf_counter() - t0, 6)
        finished_run_at = _now_iso()

        metric_value = None
        feasible = None
        feasible_best = None
        best_total_score = None
        best_total_cost = None
        best_hard_total = None
        total_cost = None
        total_score = None
        hard_total = None
        best_eval: Dict[str, Any] = {}
        iteration_count = 0
        eval_calls_total = 0
        eval_per_sec = None
        convergence_rows: List[Dict[str, Any]] = []

        if status == "ok":
            metric_value, best_eval = _extract_best_metrics(response, metric)
            best_total_cost = _as_float(best_eval.get("total_cost"))
            best_total_score = _as_float(best_eval.get("total_score"))
            best_hard_total = _hard_total_from_eval(best_eval)

            feasible_best = best_eval.get("feasible")
            if feasible_best is None and best_hard_total is not None:
                feasible_best = bool(best_hard_total == 0.0)
            elif best_hard_total is not None and best_hard_total == 0.0:
                feasible_best = True

            feasible = _first_not_none(best_eval.get("feasible"), feasible_best)
            total_cost = best_total_cost
            total_score = best_total_score
            hard_total = best_hard_total
            eval_calls_total = max(0, _as_int(response.get("eval_calls_total"), 0))

            iterations_data = response.get("iterations")
            if isinstance(iterations_data, list):
                iteration_count = len(iterations_data)
                best_so_far_metric = None
                best_so_far_score = None
                best_so_far_cost = None
                best_so_far_hard = None
                prev_eval_calls = 0
                for iter_item in iterations_data:
                    if not isinstance(iter_item, dict):
                        continue
                    label = (
                        (iter_item.get("progress") or {}).get("label")
                        if isinstance(iter_item.get("progress"), dict)
                        else ""
                    )
                    iter_no = _as_int(iter_item.get("iteration_no"), 0)
                    current_metric = _extract_metric_from_iteration(iter_item, metric)
                    current_score = _extract_metric_from_iteration(iter_item, "total_score")
                    current_cost = _extract_metric_from_iteration(iter_item, "total_cost")
                    current_hard = _extract_metric_from_iteration(iter_item, "hard_total")

                    if current_metric is not None:
                        best_so_far_metric = (
                            current_metric if best_so_far_metric is None else min(best_so_far_metric, current_metric)
                        )
                    if current_score is not None:
                        best_so_far_score = (
                            current_score if best_so_far_score is None else min(best_so_far_score, current_score)
                        )
                    if current_cost is not None:
                        best_so_far_cost = current_cost if best_so_far_cost is None else min(best_so_far_cost, current_cost)
                    if current_hard is not None:
                        best_so_far_hard = current_hard if best_so_far_hard is None else min(best_so_far_hard, current_hard)

                    iter_eval_calls = _first_not_none(
                        iter_item.get("eval_calls_total"),
                        (iter_item.get("progress") or {}).get("eval_calls_total")
                        if isinstance(iter_item.get("progress"), dict)
                        else None,
                    )
                    iter_eval_calls = _as_int(iter_eval_calls, 0)
                    if iter_eval_calls <= 0:
                        iter_eval_calls = iter_no
                    iter_eval_calls = max(iter_eval_calls, prev_eval_calls)
                    prev_eval_calls = iter_eval_calls
                    eval_calls_total = max(eval_calls_total, iter_eval_calls)

                    convergence_rows.append(
                        {
                            "job_key": job_key,
                            "method": method,
                            "n_iter": n_iter,
                            "run_id": run_idx,
                            "run": run_idx,
                            "seed": seed,
                            "outer_iter": _outer_iter_from_label(str(label or ""), iter_no),
                            "iteration_no": iter_no if iter_no > 0 else iter_item.get("iteration_no"),
                            "label": label,
                            "eval_calls_total": iter_eval_calls,
                            "metric": metric,
                            "metric_value": current_metric,
                            "best_so_far_metric_value": best_so_far_metric,
                            "current_total_score": current_score,
                            "best_so_far_total_score": best_so_far_score,
                            "current_total_cost": current_cost,
                            "best_so_far_total_cost": best_so_far_cost,
                            "current_hard_total": current_hard,
                            "best_so_far_hard_total": best_so_far_hard,
                            # Backward-compatible aliases for existing tooling.
                            "total_score": current_score,
                            "total_cost": current_cost,
                            "hard_total": current_hard,
                            "feasible": iter_item.get("feasible"),
                        }
                    )
        if eval_calls_total > 0 and elapsed_sec > 0:
            eval_per_sec = round(eval_calls_total / elapsed_sec, 6)

        run_row = {
            "job_key": job_key,
            "method": method,
            "n_iter": n_iter,
            "run_id": run_idx,
            "run": run_idx,
            "seed": seed,
            "metric": metric,
            "metric_value": metric_value,
            "feasible": feasible,
            "feasible_best": feasible_best,
            "best_total_score": best_total_score,
            "best_total_cost": best_total_cost,
            "best_hard_total": best_hard_total,
            "total_score": total_score,
            "total_cost": total_cost,
            "hard_total": hard_total,
            "iteration_count": iteration_count,
            "eval_calls_total": eval_calls_total,
            "eval_per_sec": eval_per_sec,
            "elapsed_sec": elapsed_sec,
            "status": status,
            "error": error_msg,
            "started_at": started_run_at,
            "finished_at": finished_run_at,
            "run_dir": str(run_dir),
            "request_payload_json": _safe_json_dumps(optimize_payload),
        }

        '''
        response_path = run_dir / "response.json"
        if status == "ok":
            _atomic_write_json(response_path, response)
        else:
            _atomic_write_json(response_path, {"error": error_msg})
        '''

        _atomic_write_json(run_dir / "run_record.json", run_row)
        if convergence_rows:
            _write_csv(run_dir / "convergence.csv", convergence_rows, CONVERGENCE_FIELDS)

        _append_csv_rows(run_csv, [run_row], RUN_RESULTS_FIELDS)
        _append_csv_rows(conv_csv, convergence_rows, CONVERGENCE_FIELDS)
        _append_jsonl(
            event_log_path,
            {
                "at": finished_run_at,
                "event": "run_completed",
                "job_key": job_key,
                "status": status,
                "elapsed_sec": elapsed_sec,
            },
        )

        run_rows.append(run_row)
        completed_keys.add(job_key)

        checkpoint["completed_jobs"] = len(completed_keys)
        checkpoint["remaining_jobs"] = max(total_jobs - len(completed_keys), 0)
        checkpoint["last_completed_job"] = {"job_key": job_key, "method": method, "n_iter": n_iter, "run": run_idx}
        checkpoint["updated_at"] = _now_iso()
        _atomic_write_json(checkpoint_path, checkpoint)

        # Keep tables refreshed for crash-safe progress inspection.
        _refresh_tables(
            run_rows=run_rows,
            metric=metric,
            runs_per_n=runs_per_n,
            csv_dir=csv_dir,
            table_metrics=table_metrics,
        )

        print(
            f"[{done_counter}/{total_jobs}] method={method} n={n_iter} run={run_idx}/{runs_per_n} "
            f"status={status} elapsed={elapsed_sec}s"
        )
        if error_msg:
            print(f"  error: {error_msg}", file=sys.stderr)

    # Final summaries/tables from current plan rows.
    table_files = _refresh_tables(
        run_rows=run_rows,
        metric=metric,
        runs_per_n=runs_per_n,
        csv_dir=csv_dir,
        table_metrics=table_metrics,
    )

    convergence_rows_all = _read_convergence_rows_filtered(conv_csv, plan_keys)
    fairness_files = _refresh_fairness_tables(
        run_rows=run_rows,
        convergence_rows=convergence_rows_all,
        csv_dir=csv_dir,
    )
    plot_files: Dict[str, str] = {}
    if generate_plots:
        summary_rows = _load_csv_rows(summary_csv)
        plot_files = _generate_plots(
            summary_rows=summary_rows,
            convergence_rows=convergence_rows_all,
            run_rows=run_rows,
            metric=metric,
            plots_dir=plots_dir,
            dpi=plot_dpi,
        )

    finished_sec = round(time.perf_counter() - started, 3)
    finished_at = _now_iso()
    checkpoint["status"] = "completed"
    checkpoint["current_job"] = None
    checkpoint["completed_at"] = finished_at
    checkpoint["updated_at"] = finished_at
    _atomic_write_json(checkpoint_path, checkpoint)

    manifest = {
        "created_at": started_at,
        "completed_at": finished_at,
        "api_base_url": api_base_url,
        "frame_id": frame_id,
        "input_path": str(input_path),
        "methods": methods,
        "iterations": iterations,
        "runs_per_n": runs_per_n,
        "metric": metric,
        "ui_params": ui_params,
        "common_params": common_params,
        "method_params": method_params,
        "total_jobs": total_jobs,
        "completed_jobs": len(completed_keys),
        "elapsed_sec": finished_sec,
        "output_root": str(output_root),
        "files": {
            "run_csv": str(run_csv),
            "convergence_csv": str(conv_csv),
            "params_csv": str(params_csv),
            "summary_by_method_n_csv": str(summary_csv),
            "summary_by_method_csv": str(summary_method_csv),
            "checkpoint_json": str(checkpoint_path),
            "events_ndjson": str(event_log_path),
            **table_files,
            **fairness_files,
            **plot_files,
        },
    }
    _atomic_write_json(output_root / "manifest.json", manifest)

    print("\nBenchmark completed.")
    print(f"- Output root: {output_root}")
    print(f"- Checkpoint: {checkpoint_path}")
    print(f"- Run CSV: {run_csv}")
    print(f"- Convergence CSV: {conv_csv}")
    print(f"- Summary CSV: {summary_csv}")
    if generate_plots:
        print(f"- Plots dir: {plots_dir}")
    return manifest


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="API loop benchmark with resume/checkpoint, tables, and plots."
    )
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--input", default="Doc/SampleData/example_input.json")
    parser.add_argument("--outdir", default="Doc/api_benchmark_outputs")
    parser.add_argument("--iterations", default="10,20,30,50,100,200")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--metric", default=DEFAULT_METRIC)
    parser.add_argument("--methods", default="", help="Comma list. Empty => auto-discover.")
    parser.add_argument("--exclude-methods", default="greedy", help="Comma list.")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resume-dir", default="")
    parser.add_argument("--generate-plots", action="store_true")
    parser.add_argument("--plot-dpi", type=int, default=DEFAULT_PLOT_DPI)
    parser.add_argument(
        "--config",
        "--ui-config",
        dest="ui_config",
        default=DEFAULT_CONFIG_PATH,
        help="JSON config file. Default: Doc/benchmark_test_params.json",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        cfg_path = Path(args.ui_config) if args.ui_config else None
        ui_cfg = _load_ui_config(cfg_path)
        settings = _merge_runtime_settings(args, ui_cfg)

        # CLI methods fallback if config does not define include list.
        if not settings["include_methods"] and args.methods:
            settings["include_methods"] = [m.strip() for m in args.methods.split(",") if m.strip()]

        # If CLI explicitly requests plots, enforce it.
        if args.generate_plots:
            settings["generate_plots"] = True

        run_api_benchmark(settings)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
