#!/usr/bin/env python3
"""
Reduce a full ProblemFrame JSON into a smaller, active-set-focused dataset.

Focus sets:
- shortage/backlog products
- highest inventory products
- machines with most mold changes

This script is designed to post-process the output of dataBuilder.py
and create a lighter dataset for faster testing.

Example:
  python3 Doc/SampleData/dataBuilder_active_set.py \
    --input Doc/SampleData/example_input.json \
    --state resut.json \
    --output Doc/SampleData/example_input_active_set.json \
    --top-shortage 6 --top-inventory 6 --top-machines 5
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "example_input.json"
DEFAULT_OUTPUT = BASE_DIR / "example_input_active_set.json"


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_state(obj: Dict[str, Any]) -> Dict[str, Any]:
    if "state" in obj and isinstance(obj["state"], dict):
        return obj["state"]
    if "best_state" in obj and isinstance(obj["best_state"], dict):
        return obj["best_state"]
    return {}


def parse_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val).date()
        except ValueError:
            try:
                return date.fromisoformat(val)
            except ValueError:
                return None
    return None


def parse_datetime(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None
    return None


def sort_buckets(time_buckets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(time_buckets, key=lambda tb: tb.get("index", 0))


def find_bucket_for_date(dt_date: date, time_buckets: List[Dict[str, Any]]) -> Optional[str]:
    for tb in time_buckets:
        start = parse_date(tb.get("start_date"))
        end = parse_date(tb.get("end_date"))
        if start and end and start <= dt_date <= end:
            return tb.get("id")
    return None


def build_bucket_index(time_buckets: List[Dict[str, Any]]) -> Dict[str, int]:
    return {tb.get("id"): i for i, tb in enumerate(time_buckets) if tb.get("id") is not None}


def compute_product_metrics(
    problem: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float], Dict[str, float]]:
    """
    Returns (backlog_score, inventory_score, total_demand, total_stock)
    computed per product code.
    """
    state = state or {}

    time_buckets = sort_buckets(problem.get("time_buckets", []))
    bucket_ids = [tb.get("id") for tb in time_buckets if tb.get("id")]

    products = [p.get("code") for p in problem.get("products", []) if p.get("code")]
    backlog_score = {p: 0.0 for p in products}
    inventory_score = {p: 0.0 for p in products}
    total_demand = {p: 0.0 for p in products}
    total_stock = {p: 0.0 for p in products}

    stock_by_product: Dict[str, float] = defaultdict(float)
    for st in problem.get("stocks", []):
        pcode = st.get("product_code")
        if pcode:
            stock_by_product[pcode] += float(st.get("qty") or 0)

    demand_by_product: Dict[str, Dict[str, float]] = {
        p: {bid: 0.0 for bid in bucket_ids} for p in products
    }

    for og in problem.get("orders", []):
        pcode = og.get("product_code")
        if pcode not in demand_by_product:
            continue
        for o in og.get("orders", []):
            qty = float(o.get("qty") or 0)
            bid = o.get("time_bucket_id") or o.get("week")
            if not bid and o.get("due_date"):
                due = parse_date(o.get("due_date"))
                if due:
                    bid = find_bucket_for_date(due, time_buckets)
            if bid and bid in demand_by_product[pcode]:
                demand_by_product[pcode][bid] += qty
                total_demand[pcode] += qty

    production_by_product: Dict[str, Dict[str, float]] = {
        p: {bid: 0.0 for bid in bucket_ids} for p in products
    }

    for lot in state.get("lots", []):
        pcode = lot.get("product_code")
        if pcode not in production_by_product:
            continue
        bid = lot.get("time_bucket_id")
        if not bid and lot.get("process_start_time"):
            dt = parse_datetime(lot.get("process_start_time"))
            if dt:
                bid = find_bucket_for_date(dt.date(), time_buckets)
        if bid and bid in production_by_product[pcode]:
            production_by_product[pcode][bid] += float(lot.get("qty") or 0)

    for pcode in products:
        total_stock[pcode] = float(stock_by_product.get(pcode, 0.0))
        prev = total_stock[pcode]
        max_close = prev
        backlog = 0.0
        for bid in bucket_ids:
            close = prev + production_by_product[pcode][bid] - demand_by_product[pcode][bid]
            if close < 0:
                backlog += -close
            if close > max_close:
                max_close = close
            prev = close
        backlog_score[pcode] = backlog
        inventory_score[pcode] = max_close

    return backlog_score, inventory_score, total_demand, total_stock


def compute_mold_change_counts(
    problem: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Counts mold changes per machine based on lots."""
    state = state or {}
    time_buckets = sort_buckets(problem.get("time_buckets", []))
    bucket_index = build_bucket_index(time_buckets)

    by_machine: Dict[str, List[Tuple[Tuple[int, str], Optional[str]]]] = defaultdict(list)
    for lot in state.get("lots", []):
        assigned = lot.get("assigned_resources", {}) or {}
        mid = assigned.get("machine")
        if mid is None:
            continue
        mold = assigned.get("mold")

        bid = lot.get("time_bucket_id")
        if not bid and lot.get("process_start_time"):
            dt = parse_datetime(lot.get("process_start_time"))
            if dt:
                bid = find_bucket_for_date(dt.date(), time_buckets)
        idx = bucket_index.get(bid, 0)

        t = lot.get("process_start_time") or ""
        by_machine[str(mid)].append(((idx, t), mold))

    counts: Dict[str, int] = defaultdict(int)
    for mid, events in by_machine.items():
        events.sort(key=lambda x: x[0])
        prev = None
        count = 0
        for _, mold in events:
            if prev is not None and mold is not None and mold != prev:
                count += 1
            if mold is not None:
                prev = mold
        counts[mid] = count
    return counts


def compute_compat_mold_counts(problem: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, Set[str]] = defaultdict(set)
    for pair in problem.get("compatibility", {}).get("machine_mold_pairs", []):
        mid = str(pair.get("machine_id"))
        mold = pair.get("mold_code")
        if mid and mold:
            counts[mid].add(mold)
    return {mid: len(molds) for mid, molds in counts.items()}


def top_keys(scores: Dict[str, float], limit: int, min_positive: bool = False) -> List[str]:
    items = [(k, v) for k, v in scores.items()]
    if min_positive:
        items = [(k, v) for k, v in items if v > 0]
    items.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return [k for k, _ in items[: max(0, limit)]]


def build_products_by_process(problem: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = defaultdict(list)
    for p in problem.get("products", []):
        pcode = p.get("code")
        if not pcode:
            continue
        for step in p.get("process_data", []) or []:
            proc = step.get("process_code")
            if proc:
                out[proc].append(pcode)
    return out


def build_products_by_process_mold(problem: Dict[str, Any]) -> Dict[Tuple[str, str], Set[str]]:
    out: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for pm in problem.get("compatibility", {}).get("product_molds", []):
        pcode = pm.get("product_code")
        proc = pm.get("process_code")
        if not pcode or not proc:
            continue
        for mold in pm.get("allowed_molds", []) or []:
            out[(proc, mold)].add(pcode)
    return out


def select_products_for_machine(
    problem: Dict[str, Any],
    machine_id: str,
    ranked_products: List[str],
    limit: int,
) -> List[str]:
    pairs = problem.get("compatibility", {}).get("machine_mold_pairs", [])
    product_by_proc_mold = build_products_by_process_mold(problem)
    candidates: Set[str] = set()
    for pair in pairs:
        if str(pair.get("machine_id")) != machine_id:
            continue
        key = (pair.get("process_code"), pair.get("mold_code"))
        candidates |= product_by_proc_mold.get(key, set())

    if not candidates:
        products_by_process = build_products_by_process(problem)
        for pair in pairs:
            if str(pair.get("machine_id")) != machine_id:
                continue
            proc = pair.get("process_code")
            if proc:
                candidates |= set(products_by_process.get(proc, []))

    if not candidates:
        return []

    ranked_set = [p for p in ranked_products if p in candidates]
    if not ranked_set:
        ranked_set = sorted(candidates)
    return ranked_set[: max(0, limit)]


def rank_products(
    backlog: Dict[str, float],
    inventory: Dict[str, float],
    demand: Dict[str, float],
    stock: Dict[str, float],
) -> List[str]:
    items = []
    for pcode in backlog.keys():
        items.append(
            (
                pcode,
                backlog.get(pcode, 0.0),
                inventory.get(pcode, 0.0),
                demand.get(pcode, 0.0),
                stock.get(pcode, 0.0),
            )
        )
    items.sort(key=lambda x: (x[1], x[2], x[3], x[4], x[0]), reverse=True)
    return [i[0] for i in items]


def filter_state(
    state: Dict[str, Any],
    selected_products: Set[str],
    selected_machines: Set[str],
    selected_molds: Set[str],
    bucket_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    if not state:
        return {"meta": {"iteration": 0}, "lots": []}
    out = dict(state)

    lots_out = []
    for lot in state.get("lots", []):
        if lot.get("product_code") not in selected_products:
            continue
        if bucket_ids and lot.get("time_bucket_id") and lot.get("time_bucket_id") not in bucket_ids:
            continue
        assigned = lot.get("assigned_resources", {}) or {}
        mid = assigned.get("machine")
        mold = assigned.get("mold")
        if mid is not None and str(mid) not in selected_machines:
            continue
        if selected_molds and mold is not None and mold not in selected_molds:
            continue
        lots_out.append(lot)
    out["lots"] = lots_out

    inv_out = []
    for inv in state.get("inventory_summary", []):
        if inv.get("product_code") not in selected_products:
            continue
        if bucket_ids:
            bid = inv.get("time_bucket_id") or inv.get("week")
            if bid and bid not in bucket_ids:
                continue
        inv_out.append(inv)
    if inv_out:
        out["inventory_summary"] = inv_out
    return out


def reduce_problem(
    problem: Dict[str, Any],
    selected_products: Set[str],
    selected_machines: Set[str],
    top_machines: Set[str],
    max_buckets: int = 0,
) -> Dict[str, Any]:
    time_buckets = sort_buckets(problem.get("time_buckets", []))
    if max_buckets and max_buckets > 0:
        time_buckets = time_buckets[: max_buckets]
    bucket_ids = [tb.get("id") for tb in time_buckets if tb.get("id")]
    bucket_set = set(bucket_ids)

    # orders
    orders_out = []
    for og in problem.get("orders", []):
        pcode = og.get("product_code")
        if pcode not in selected_products:
            continue
        orders = []
        for o in og.get("orders", []):
            bid = o.get("time_bucket_id") or o.get("week")
            if not bid and o.get("due_date"):
                dd = parse_date(o.get("due_date"))
                if dd:
                    bid = find_bucket_for_date(dd, time_buckets)
            if bucket_set and bid and bid not in bucket_set:
                continue
            orders.append(o)
        if orders:
            og_out = dict(og)
            og_out["orders"] = orders
            orders_out.append(og_out)

    # stocks
    stocks_out = [st for st in problem.get("stocks", []) if st.get("product_code") in selected_products]

    # products
    products_out = [p for p in problem.get("products", []) if p.get("code") in selected_products]

    # process codes from products
    process_codes: Set[str] = set()
    for p in products_out:
        for step in p.get("process_data", []) or []:
            if step.get("process_code"):
                process_codes.add(step.get("process_code"))

    # compatibility filtering
    compat = problem.get("compatibility", {})
    product_molds_out = []
    selected_molds: Set[str] = set()
    for pm in compat.get("product_molds", []) or []:
        pcode = pm.get("product_code")
        if pcode not in selected_products:
            continue
        proc = pm.get("process_code")
        if proc and proc not in process_codes:
            continue
        molds = [m for m in pm.get("allowed_molds", []) or []]
        if not molds:
            continue
        selected_molds.update(molds)
        pm_out = dict(pm)
        pm_out["allowed_molds"] = molds
        product_molds_out.append(pm_out)

    machine_mold_pairs_out = []
    compat_pairs = compat.get("machine_mold_pairs", []) or []
    for pair in compat_pairs:
        mid = str(pair.get("machine_id"))
        if mid not in selected_machines and mid not in top_machines:
            continue
        if pair.get("process_code") and pair.get("process_code") not in process_codes:
            continue
        mold = pair.get("mold_code")
        if selected_molds and mold not in selected_molds:
            continue
        machine_mold_pairs_out.append(pair)

    if not selected_molds:
        selected_molds = {p.get("mold_code") for p in machine_mold_pairs_out if p.get("mold_code")}

    # refine selected machines based on filtered pairs
    pair_machine_ids = {str(p.get("machine_id")) for p in machine_mold_pairs_out if p.get("machine_id") is not None}
    selected_machines_final = set(selected_machines) | set(top_machines) | pair_machine_ids

    # resources
    machines_out = []
    for m in problem.get("resources", {}).get("machines", []) or []:
        if str(m.get("id")) not in selected_machines_final:
            continue
        if bucket_set and m.get("capacity_by_bucket"):
            cap = {k: v for k, v in m.get("capacity_by_bucket", {}).items() if k in bucket_set}
            m = dict(m)
            m["capacity_by_bucket"] = cap
        machines_out.append(m)

    molds_out = []
    for mold in problem.get("resources", {}).get("molds", []) or []:
        if selected_molds and mold.get("code") not in selected_molds:
            continue
        molds_out.append(mold)

    # processes (add from machines/molds)
    for m in machines_out:
        if m.get("process_code"):
            process_codes.add(m.get("process_code"))
    for mold in molds_out:
        if mold.get("process_code"):
            process_codes.add(mold.get("process_code"))

    processes_out = [p for p in problem.get("processes", []) if p.get("code") in process_codes]

    # materials
    material_codes: Set[str] = set(selected_products)
    for p in products_out:
        for step in p.get("process_data", []) or []:
            for inp in step.get("inputs", []) or []:
                if inp.get("material_code"):
                    material_codes.add(inp.get("material_code"))
            for inp in step.get("process_input", []) or []:
                if inp.get("material_code"):
                    material_codes.add(inp.get("material_code"))
    materials_out = [m for m in problem.get("materials", []) if m.get("code") in material_codes]

    # work calendar
    work_calendar = problem.get("work_calendar", [])
    if bucket_set and time_buckets:
        start = parse_date(time_buckets[0].get("start_date"))
        end = parse_date(time_buckets[-1].get("end_date"))
        if start and end:
            work_calendar = [e for e in work_calendar if start <= parse_date(e.get("date")) <= end]

    return {
        "problem_meta": problem.get("problem_meta", {}),
        "time_buckets": time_buckets,
        "orders": orders_out,
        "stocks": stocks_out,
        "materials": materials_out,
        "products": products_out,
        "processes": processes_out,
        "resources": {"machines": machines_out, "molds": molds_out},
        "shift_templates": problem.get("shift_templates", []),
        "work_calendar": work_calendar,
        "compatibility": {
            "machine_mold_pairs": machine_mold_pairs_out,
            "product_molds": product_molds_out,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reduce ProblemFrame input with active-set focus.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Full input JSON (from dataBuilder).")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Reduced output JSON.")
    parser.add_argument("--state", default=None, help="Optional state/solution JSON (for mold-change counts).")
    parser.add_argument("--top-shortage", type=int, default=6)
    parser.add_argument("--top-inventory", type=int, default=6)
    parser.add_argument("--top-machines", type=int, default=5)
    parser.add_argument("--products-per-machine", type=int, default=1)
    parser.add_argument("--max-products", type=int, default=0, help="Optional cap on total products.")
    parser.add_argument("--max-buckets", type=int, default=0, help="Optional cap on time buckets.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"Missing input file: {input_path}")
        return 1

    frame = load_json(input_path)
    problem = frame.get("problemData", {})
    state_for_metrics = extract_state(frame)

    if args.state:
        state_path = Path(args.state)
        if not state_path.exists():
            print(f"Missing state file: {state_path}")
            return 1
        state_for_metrics = extract_state(load_json(state_path))

    backlog, inventory, demand, stock = compute_product_metrics(problem, state_for_metrics)
    ranked_products = rank_products(backlog, inventory, demand, stock)

    shortage_top = top_keys(backlog, args.top_shortage, min_positive=True)
    if not shortage_top:
        shortage_top = top_keys(demand, args.top_shortage, min_positive=True)
    if not shortage_top:
        shortage_top = top_keys(demand, args.top_shortage, min_positive=False)

    inventory_top = top_keys(inventory, args.top_inventory, min_positive=True)
    if not inventory_top:
        inventory_top = top_keys(stock, args.top_inventory, min_positive=True)
    if not inventory_top:
        inventory_top = top_keys(stock, args.top_inventory, min_positive=False)

    selected_products: Set[str] = set(shortage_top) | set(inventory_top)

    mold_change_counts = compute_mold_change_counts(problem, state_for_metrics)
    if any(v > 0 for v in mold_change_counts.values()):
        machine_scores = {k: float(v) for k, v in mold_change_counts.items()}
    else:
        compat_counts = compute_compat_mold_counts(problem)
        machine_scores = {k: float(v) for k, v in compat_counts.items()}

    top_machines = set(top_keys(machine_scores, args.top_machines, min_positive=False))

    # add products to cover top machines
    for mid in top_machines:
        if args.products_per_machine <= 0:
            continue
        add = select_products_for_machine(problem, mid, ranked_products, args.products_per_machine)
        selected_products.update(add)

    if not selected_products:
        selected_products = set(ranked_products[: max(args.top_shortage, args.top_inventory, 1)])

    # optional cap on products
    if args.max_products and args.max_products > 0:
        ranked = [p for p in ranked_products if p in selected_products]
        selected_products = set(ranked[: args.max_products])

    # derive machines required by compatibility for selected products
    selected_product_set = set(selected_products)
    compat = problem.get("compatibility", {})
    product_molds = [pm for pm in compat.get("product_molds", []) or [] if pm.get("product_code") in selected_product_set]
    selected_molds = {m for pm in product_molds for m in (pm.get("allowed_molds", []) or [])}

    required_machine_ids: Set[str] = set()
    for pair in compat.get("machine_mold_pairs", []) or []:
        if pair.get("mold_code") in selected_molds:
            required_machine_ids.add(str(pair.get("machine_id")))

    selected_machines = set(required_machine_ids) | set(top_machines)

    if not selected_machines and selected_products:
        product_processes: Set[str] = set()
        for p in problem.get("products", []) or []:
            if p.get("code") not in selected_products:
                continue
            for step in p.get("process_data", []) or []:
                if step.get("process_code"):
                    product_processes.add(step.get("process_code"))
        for m in problem.get("resources", {}).get("machines", []) or []:
            if m.get("process_code") in product_processes:
                selected_machines.add(str(m.get("id")))

    reduced_problem = reduce_problem(
        problem,
        selected_products,
        selected_machines,
        top_machines=top_machines,
        max_buckets=args.max_buckets,
    )

    reduced_molds = {
        mold
        for pm in reduced_problem.get("compatibility", {}).get("product_molds", []) or []
        for mold in pm.get("allowed_molds", []) or []
    }
    if not reduced_molds:
        reduced_molds = {
            m.get("code")
            for m in reduced_problem.get("resources", {}).get("molds", []) or []
            if m.get("code")
        }

    reduced_state = filter_state(
        frame.get("state", {}),
        selected_products,
        selected_machines,
        reduced_molds,
        set(tb.get("id") for tb in reduced_problem.get("time_buckets", []) if tb.get("id")),
    )

    out_frame = {
        "problemData": reduced_problem,
        "scenarioConfig": frame.get("scenarioConfig", {}),
        "state": reduced_state,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out_frame, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Active-set reduction complete.")
    print(f"Products: {len(problem.get('products', []))} -> {len(reduced_problem.get('products', []))}")
    print(f"Machines: {len(problem.get('resources', {}).get('machines', []))} -> {len(reduced_problem.get('resources', {}).get('machines', []))}")
    print(f"Molds: {len(problem.get('resources', {}).get('molds', []))} -> {len(reduced_problem.get('resources', {}).get('molds', []))}")
    print(f"Orders: {len(problem.get('orders', []))} -> {len(reduced_problem.get('orders', []))}")
    print(f"Time buckets: {len(problem.get('time_buckets', []))} -> {len(reduced_problem.get('time_buckets', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
