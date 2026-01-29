# ============================================================
#  Sezgisel Üretim Planlama
#  Evaluation Context + Constraints + Objective (Total Cost)
#  FULL PSEUDOCODE (patched)
#
#  PATCH SUMMARY
#  - idx resource reset bug FIX: resource_by_id/resources_by_type yeniden boşlanmıyor
#  - time_layer timedelta FIX: date/datetime + timedelta(days=1)
#  - segment_code canonical FIX: usage_shift/sequence segment_code ile tutulur
#  - due_date -> bucket mapping FIX: demand_by_bucket doldurulur
#  - build_segments_for_date() eklendi (capacity override için)
#  - changeovers, hard/soft evaluate_constraints(), total_cost()
# ============================================================

from datetime import datetime, date, timedelta
from bisect import bisect_right

INF = 10**18
ONE_DAY = timedelta(days=1)


# ============================================================
# 0) Common Helpers
# ============================================================

def ensure_dict(d, *keys, default_factory=dict):
    """Nested dict helper: ensure d[k1][k2]...[kn] exists and return it."""
    cur = d
    for k in keys[:-1]:
        if k not in cur:
            cur[k] = default_factory()
        cur = cur[k]
    last = keys[-1]
    if last not in cur:
        cur[last] = default_factory()
    return cur[last]


def ceil_div(a, b):
    return (a + b - 1) // b


def combine(d: date, hhmm: str) -> datetime:
    """combine(date, 'HH:MM') -> datetime"""
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m)


def as_date(x):
    """datetime/date -> date"""
    if x is None:
        return None
    return x.date() if hasattr(x, "date") else x


def datetime_to_bucket_id(dt, time_buckets):
    """Find the output bucket that contains dt.date()"""
    d = as_date(dt)
    for tb in time_buckets:
        if tb["start_date"] <= d <= tb["end_date"]:
            return tb["id"]
    raise ValueError(f"datetime {dt} does not fit into any time_bucket")


def find_step(process_steps, process_code):
    for s in process_steps:
        if s.get("process_code") == process_code:
            return s
    return None


# ============================================================
# 1) Resource Indexes
# ============================================================

def build_resource_indexes(ctx):
    problem = ctx["problem"]
    idx = ctx["idx"]

    # Initialize ONLY here (do NOT reset later)
    idx["resource_by_id"] = {}
    idx["resources_by_type"] = {}

    def add_resource(res):
        rid = res["id"]
        t = res["type_code"]
        idx["resource_by_id"][rid] = res
        idx["resources_by_type"].setdefault(t, []).append(res)

    resources = problem.get("resources", {})

    # Machines
    for m in resources.get("machines", []):
        add_resource({
            "id": m["id"],
            "type_code": "machine",
            "process_code": m.get("process_code"),
            "attributes": {
                "capacity_by_bucket": m.get("capacity_by_bucket") or {},
                "shift_templates_code": m.get("shift_templates_code"),  # optional override
            }
        })

    # Molds (id olarak code)
    for mold in resources.get("molds", []):
        add_resource({
            "id": mold["code"],
            "type_code": "mold",
            "process_code": mold.get("process_code"),
            "attributes": {
                "cavities": mold.get("eye"),
                "compatible_machines": mold.get("compatible_machines_id", []),
                "supported_products": mold.get("supported_products_id", []),
            }
        })

    # Generic others: benches/operators/tools...
    for tname, arr in resources.items():
        if tname in ("machines", "molds"):
            continue
        for r in arr:
            add_resource({
                "id": r["id"],
                "type_code": tname.rstrip("s"),  # benches -> bench
                "process_code": r.get("process_code"),
                "attributes": r.get("attributes", {}),
            })


# ============================================================
# 2) Index Builder (PATCHED: no resource reset)
# ============================================================

def build_indexes(ctx):
    problem = ctx["problem"]
    idx = ctx["idx"]

    idx["product_by_code"] = {p["code"]: p for p in problem.get("products", [])}
    idx["material_by_code"] = {m["code"]: m for m in problem.get("materials", [])}
    idx["process_by_code"] = {pr["code"]: pr for pr in problem.get("processes", [])}

    # resources
    build_resource_indexes(ctx)

    # product -> routing/steps
    idx["process_steps_by_product"] = {}
    for p in problem.get("products", []):
        idx["process_steps_by_product"][p["code"]] = p.get("process_data", [])

    # time buckets
    idx["time_bucket_by_id"] = {tb["id"]: tb for tb in problem.get("time_buckets", [])}

    # compatibility indices
    idx["machine_mold_pairs"] = {}
    for pair in problem.get("compatibility", {}).get("machine_mold_pairs", []):
        idx["machine_mold_pairs"][(pair["machine_id"], pair["mold_code"], pair.get("process_code"))] = True

    idx["product_molds"] = {}
    for pm in problem.get("compatibility", {}).get("product_molds", []):
        key = (pm["product_code"], pm.get("process_code"))
        idx["product_molds"][key] = set(pm.get("allowed_molds", []))


# ============================================================
# 3) Time Layer (calendar + shifts) (PATCHED)
#    - timedelta fixes
#    - build_segments_for_date defined
#    - segment_of_datetime cross-midnight safe
# ============================================================

def build_segments_for_date(tl, d: date, tpl_code: str):
    tpl = tl["shift_template_by_code"][tpl_code]
    segments = []
    for seg in tpl.get("segments", []):
        start_dt = combine(d, seg["start"])
        end_dt = combine(d, seg["end"])
        # cross-midnight (end <= start) or end == 00:00
        if seg["end"] == "00:00" or end_dt <= start_dt:
            end_dt = end_dt + ONE_DAY
        segments.append({
            "segment_code": seg["code"],
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "duration_hours": (end_dt - start_dt).total_seconds() / 3600.0,
            "constraint_codes": seg.get("constraints", []),
        })
    return segments


def build_time_layer(ctx):
    problem = ctx["problem"]
    tl = ctx["time_layer"]

    # 1) Output buckets
    tl["time_buckets"] = problem.get("time_buckets", [])
    tl["bucket_by_id"] = {tb["id"]: tb for tb in tl["time_buckets"]}
    tl["time_index_by_id"] = {tb["id"]: tb.get("index") for tb in tl["time_buckets"]}

    # date -> bucket_id cache
    tl["bucket_of_date"] = {}
    for tb in tl["time_buckets"]:
        d = tb["start_date"]
        while d <= tb["end_date"]:
            tl["bucket_of_date"][d] = tb["id"]
            d = d + ONE_DAY

    def bucket_of_datetime(dt):
        return tl["bucket_of_date"][as_date(dt)]

    tl["bucket_of_datetime"] = bucket_of_datetime

    # 2) Shift templates + segment constraints
    tl["shift_template_by_code"] = {st["code"]: st for st in problem.get("shift_templates", [])}

    tl["segment_constraints_by_segment_code"] = {}
    for st in problem.get("shift_templates", []):
        for seg in st.get("segments", []):
            tl["segment_constraints_by_segment_code"][seg["code"]] = seg.get("constraints", [])

    # 3) Work calendar -> day_schedule_by_date
    tl["day_schedule_by_date"] = {}
    for wc in problem.get("work_calendar", []):
        d = wc["date"]
        holiday = wc.get("holiday", False)
        tpl_code = wc.get("shift_templates_code")

        ds = {
            "date": d,
            "is_holiday": holiday,
            "shift_templates_code": tpl_code,
            "segments": []
        }

        if holiday or not tpl_code:
            tl["day_schedule_by_date"][d] = ds
            continue

        ds["segments"] = build_segments_for_date(tl, d, tpl_code)
        tl["day_schedule_by_date"][d] = ds

    # helper: dt -> segment_code (cross-midnight safe)
    def segment_of_datetime(dt: datetime):
        d = dt.date()

        # 1) same-day schedule
        ds = tl["day_schedule_by_date"].get(d)
        if ds and not ds.get("is_holiday"):
            for seg in ds.get("segments", []):
                if seg["start_datetime"] <= dt < seg["end_datetime"]:
                    return seg["segment_code"]

        # 2) cross-midnight: previous day's segments may spill into today
        prev_d = d - ONE_DAY
        ds_prev = tl["day_schedule_by_date"].get(prev_d)
        if ds_prev and not ds_prev.get("is_holiday"):
            for seg in ds_prev.get("segments", []):
                if seg["start_datetime"] <= dt < seg["end_datetime"]:
                    return seg["segment_code"]

        return None

    tl["segment_of_datetime"] = segment_of_datetime


# ============================================================
# 4) Process Layer (process -> resource roles)
# ============================================================

def build_process_layer(ctx):
    problem = ctx["problem"]
    idx = ctx["idx"]
    pl = ctx["process_layer"]

    pl["resource_roles_by_process"] = {}

    for pr in problem.get("processes", []):
        roles = []
        # pr["constraints"] e.g. ["machine","mold"]
        for c in pr.get("constraints", []):
            roles.append({
                "role_code": c,      # role = type (baseline)
                "type_code": c,
                "required": True,
                "multiplicity": 1,
                "attributes": {}
            })
        pl["resource_roles_by_process"][pr["code"]] = roles

    # also put into idx for fast access
    idx["resource_roles_by_process"] = pl["resource_roles_by_process"]


# ============================================================
# 5) Order Layer (due-date hard)
#    (PATCHED: do not overwrite list)
# ============================================================

def build_order_layer(ctx):
    problem = ctx["problem"]
    state = ctx["state"]
    ol = ctx["order_layer"]

    # orders_by_product
    ol["orders_by_product"] = {}
    for og in problem.get("orders", []):
        pcode = og["product_code"]
        lst = ol["orders_by_product"].setdefault(pcode, [])
        for o in og.get("orders", []):
            # expected: {"order_id":..., "due_date":..., "qty":...}
            lst.append(o)

    # sorted by due_date
    ol["orders_sorted_by_due_date"] = {}
    for pcode, orders in ol["orders_by_product"].items():
        ol["orders_sorted_by_due_date"][pcode] = sorted(orders, key=lambda x: x["due_date"])

    # production events by product (from state.lots)
    ol["production_events_by_product"] = {}
    for lot in state.get("lots", []):
        pcode = lot["product_code"]
        end_time = lot.get("process_end_time") or lot.get("end_time") or lot["process_start_time"]
        qty = lot["qty"]
        ensure_dict(ol["production_events_by_product"], pcode, default_factory=list).append((end_time, qty))

    # prefix sums per product
    ol["production_cumsum_by_product"] = {}
    for pcode, events in ol["production_events_by_product"].items():
        events.sort(key=lambda x: x[0])
        cum = []
        total = 0
        for t, q in events:
            total += q
            cum.append((t, total))
        ol["production_cumsum_by_product"][pcode] = cum


# ============================================================
# 6) Product Layer: Demand + Stock (bucket-based)
#    (PATCHED: due_date -> bucket mapping)
# ============================================================

def build_product_layer_demand_and_inventory(ctx):
    problem = ctx["problem"]
    state = ctx["state"]
    tl = ctx["time_layer"]
    pl = ctx["product_layer"]

    buckets = [tb["id"] for tb in tl.get("time_buckets", [])]

    # initial stock
    pl["initial_stock"] = {p["code"]: 0.0 for p in problem.get("products", [])}
    for st in problem.get("stocks", []):
        pl["initial_stock"][st["product_code"]] += float(st["qty"])

    # production_out by bucket
    pl["production_out"] = {p["code"]: {b: 0.0 for b in buckets} for p in problem.get("products", [])}
    for lot in state.get("lots", []):
        pcode = lot["product_code"]
        if lot.get("time_bucket_id"):
            b = lot["time_bucket_id"]
        else:
            b = datetime_to_bucket_id(lot["process_start_time"], tl["time_buckets"])
        pl["production_out"][pcode][b] += float(lot["qty"])

    # demand_by_bucket (PATCHED)
    pl["demand_by_bucket"] = {p["code"]: {b: 0.0 for b in buckets} for p in problem.get("products", [])}
    for og in problem.get("orders", []):
        pcode = og["product_code"]
        for o in og.get("orders", []):
            qty = float(o["qty"])
            if o.get("time_bucket_id"):
                b = o["time_bucket_id"]
            else:
                # map due_date -> bucket using time_layer cache
                dd = as_date(o.get("due_date"))
                b = tl["bucket_of_date"].get(dd)
                # if due_date outside horizon, you can:
                # - ignore, or
                # - clamp to last bucket; here ignore safely
                if b is None:
                    continue
            pl["demand_by_bucket"][pcode][b] += qty

    # opening/closing stock
    pl["opening_stock"] = {p["code"]: {b: 0.0 for b in buckets} for p in problem.get("products", [])}
    pl["closing_stock"] = {p["code"]: {b: 0.0 for b in buckets} for p in problem.get("products", [])}

    for p in problem.get("products", []):
        pcode = p["code"]
        prev = float(pl["initial_stock"][pcode])
        for b in buckets:
            pl["opening_stock"][pcode][b] = prev
            close = prev + pl["production_out"][pcode][b] - pl["demand_by_bucket"][pcode][b]
            pl["closing_stock"][pcode][b] = close
            prev = close


# ============================================================
# 7) Product Layer: Process Flows (step outputs + material usage)
# ============================================================

def build_product_layer_process_flows(ctx):
    problem = ctx["problem"]
    state = ctx["state"]
    idx = ctx["idx"]
    tl = ctx["time_layer"]
    pl = ctx["product_layer"]

    buckets = [tb["id"] for tb in tl.get("time_buckets", [])]

    # step_output_qty[product][step_no][bucket]
    pl["step_output_qty"] = {}
    for p in problem.get("products", []):
        pcode = p["code"]
        pl["step_output_qty"][pcode] = {}
        for step in idx["process_steps_by_product"].get(pcode, []):
            step_no = step["step_no"]
            pl["step_output_qty"][pcode][step_no] = {b: 0.0 for b in buckets}

    # material_usage[material][bucket]
    pl["material_usage"] = {}
    for m in problem.get("materials", []):
        pl["material_usage"][m["code"]] = {b: 0.0 for b in buckets}

    for lot in state.get("lots", []):
        pcode = lot["product_code"]
        qty_out = float(lot["qty"])

        if lot.get("time_bucket_id"):
            b = lot["time_bucket_id"]
        else:
            b = datetime_to_bucket_id(lot["process_start_time"], tl["time_buckets"])

        steps = idx["process_steps_by_product"].get(pcode, [])
        step = find_step(steps, lot["process_code"])
        if not step:
            continue

        pl["step_output_qty"][pcode][step["step_no"]][b] += qty_out

        # inputs consumption
        yield_factor = step.get("yield_factor", 1.0) or 1.0
        for inp in step.get("inputs", []):
            mat_code = inp["material_code"]
            net_req = qty_out * float(inp["qty_per_output_unit"])
            gross_req = net_req / float(yield_factor)

            if mat_code not in pl["material_usage"]:
                pl["material_usage"][mat_code] = {bb: 0.0 for bb in buckets}
            pl["material_usage"][mat_code][b] += gross_req


# ============================================================
# 8) Resource Layer: Instances
# ============================================================

def build_resource_layer_instances(ctx):
    idx = ctx["idx"]
    rl = ctx["resource_layer"]
    rl["instances_by_id"] = idx.get("resource_by_id", {})
    rl["instances_by_type"] = idx.get("resources_by_type", {})


# ============================================================
# 9) Resource Layer: Capacity (bucket + segment)
# ============================================================

def build_resource_layer_capacity(ctx):
    rl = ctx["resource_layer"]
    tl = ctx["time_layer"]
    idx = ctx["idx"]

    buckets = [tb["id"] for tb in tl.get("time_buckets", [])]

    rl["capacity_bucket"] = {}
    rl["capacity_shift"] = {}

    for type_code, resources in idx.get("resources_by_type", {}).items():
        rl["capacity_bucket"][type_code] = {}
        rl["capacity_shift"][type_code] = {}

        for res in resources:
            rid = res["id"]

            # bucket capacity
            cap_by_bucket = (res.get("attributes", {}) or {}).get("capacity_by_bucket") or {}
            rl["capacity_bucket"][type_code][rid] = {b: float(cap_by_bucket.get(b, 0.0)) for b in buckets}

            # shift capacity: capacity_shift[type][rid][date][segment_code] = hours
            rl["capacity_shift"][type_code][rid] = {}
            res_tpl_code = (res.get("attributes", {}) or {}).get("shift_templates_code")

            for d, ds in tl.get("day_schedule_by_date", {}).items():
                if ds.get("is_holiday"):
                    continue

                segments = ds.get("segments", [])
                if res_tpl_code and ds.get("shift_templates_code") != res_tpl_code:
                    # override template
                    segments = build_segments_for_date(tl, d, res_tpl_code)

                for seg in segments:
                    seg_code = seg["segment_code"]
                    hrs = float(seg["duration_hours"])
                    ensure_dict(rl["capacity_shift"][type_code][rid], d, default_factory=dict)
                    rl["capacity_shift"][type_code][rid][d][seg_code] = hrs


# ============================================================
# 10) Resource Layer: Usage + Sequence (bucket + segment) (PATCHED)
#     - usage_shift and sequence keyed by segment_code (not shift_code)
# ============================================================

def build_resource_layer_usages_and_sequences(ctx):
    state = ctx["state"]
    idx = ctx["idx"]
    tl = ctx["time_layer"]
    rl = ctx["resource_layer"]

    buckets = [tb["id"] for tb in tl.get("time_buckets", [])]

    rl["usage_bucket"] = {}
    rl["usage_shift"] = {}
    rl["sequence"] = {}

    # init for all resources
    for type_code, resources in idx.get("resources_by_type", {}).items():
        rl["usage_bucket"][type_code] = {}
        rl["usage_shift"][type_code] = {}
        rl["sequence"][type_code] = {}

        for res in resources:
            rid = res["id"]
            rl["usage_bucket"][type_code][rid] = {b: 0.0 for b in buckets}
            rl["usage_shift"][type_code][rid] = {}   # date -> seg_code -> hours
            rl["sequence"][type_code][rid] = {}      # date -> seg_code -> [lots]

    # lots accumulate
    for lot in state.get("lots", []):
        pcode = lot["product_code"]
        process_code = lot["process_code"]
        qty = float(lot["qty"])

        steps = idx.get("process_steps_by_product", {}).get(pcode, [])
        step = find_step(steps, process_code)
        if not step:
            continue

        base_qty = float(step.get("base_qty", 1) or 1)
        cycle_sec = float(step.get("cycle_time_sec", 0) or 0)
        setup_min = float(step.get("setup_time_min", 0) or 0)

        batch_count = ceil_div(int(qty), int(base_qty)) if base_qty else 0
        total_sec = (batch_count * cycle_sec) + (setup_min * 60.0)
        total_hours = total_sec / 3600.0

        start_dt = lot["process_start_time"]
        d = start_dt.date()

        # bucket
        b = lot.get("time_bucket_id") or datetime_to_bucket_id(start_dt, tl["time_buckets"])

        # canonical segment_code
        seg_code = lot.get("segment_code") or tl["segment_of_datetime"](start_dt) or lot.get("shift_code") or "NA"

        roles = idx.get("resource_roles_by_process", {}).get(process_code, [])
        assigned = lot.get("assigned_resources", {}) or {}

        for role in roles:
            role_code = role["role_code"]
            type_code = role["type_code"]
            rid = assigned.get(role_code)

            if not rid:
                continue

            # usage by bucket
            rl["usage_bucket"][type_code][rid][b] += total_hours

            # usage by date+segment
            ensure_dict(rl["usage_shift"][type_code][rid], d, default_factory=dict)
            rl["usage_shift"][type_code][rid][d][seg_code] = rl["usage_shift"][type_code][rid][d].get(seg_code, 0.0) + total_hours

            # sequence (ordered lots)
            ensure_dict(rl["sequence"][type_code][rid], d, default_factory=dict)
            rl["sequence"][type_code][rid][d].setdefault(seg_code, []).append(lot)

    # sort sequences by start time
    for type_code, by_res in rl["sequence"].items():
        for rid, by_date in by_res.items():
            for d, by_seg in by_date.items():
                for seg_code, lots in by_seg.items():
                    lots.sort(key=lambda x: x["process_start_time"])


# ============================================================
# 11) Resource Layer: Changeovers (setup/change events)
# ============================================================

def build_changeovers_for(seq_by_res, watch_role):
    """
    seq_by_res: rl["sequence"][type_code] = {rid: {date:{seg_code:[lots...]}}}
    watch_role: e.g. "mold"
    """
    out = {}
    for rid, by_date in seq_by_res.items():
        out[rid] = {}
        for d, by_seg in by_date.items():
            out[rid][d] = {}
            for seg_code, lots in by_seg.items():
                events = []
                prev_val = None
                prev_lot = None

                for lot in lots:
                    assigned = lot.get("assigned_resources", {}) or {}
                    cur_val = assigned.get(watch_role)

                    if prev_val is not None and cur_val != prev_val:
                        events.append({
                            "type": f"{watch_role.upper()}_CHANGE",
                            "from": prev_val,
                            "to": cur_val,
                            "time": lot["process_start_time"],
                            "from_lot": prev_lot.get("lot_id") if prev_lot else None,
                            "to_lot": lot.get("lot_id"),
                        })
                    prev_val = cur_val
                    prev_lot = lot

                out[rid][d][seg_code] = events
    return out


def build_resource_layer_changeovers(ctx):
    rl = ctx["resource_layer"]
    seq = rl.get("sequence", {})
    rl["changeovers"] = {}

    # machine üzerinde mold change
    if "machine" in seq:
        rl["changeovers"]["machine"] = {}
        rl["changeovers"]["machine"]["mold"] = build_changeovers_for(seq["machine"], "mold")


# ============================================================
# 12) Metrics cache (optional)
# ============================================================

def build_metrics_cache(ctx):
    # placeholder: burada ağır hesapları cache'leyebilirsin
    ctx["metrics"]["cache_built"] = True


# ============================================================
# 13) Build Evaluation Context (main)
# ============================================================

def build_evaluation_context(problem, state, scenario):
    ctx = {
        "problem": problem,
        "state": state,
        "scenario": scenario,
        "idx": {},
        "time_layer": {},
        "product_layer": {},
        "resource_layer": {},
        "process_layer": {},
        "order_layer": {},
        "metrics": {},
    }

    build_indexes(ctx)
    build_time_layer(ctx)

    build_process_layer(ctx)
    build_order_layer(ctx)

    build_product_layer_demand_and_inventory(ctx)
    build_product_layer_process_flows(ctx)

    build_resource_layer_instances(ctx)
    build_resource_layer_capacity(ctx)
    build_resource_layer_usages_and_sequences(ctx)
    build_resource_layer_changeovers(ctx)

    build_metrics_cache(ctx)
    return ctx


# ============================================================
# 14) Constraint Evaluation + Objective
# ============================================================

# ---- Produced / Demand helpers ----

def produced_until(ctx, product_code, due_dt):
    """
    order_layer.production_cumsum_by_product[pcode] = [(t1, cum1), ...]
    """
    ol = ctx["order_layer"]
    arr = ol.get("production_cumsum_by_product", {}).get(product_code, [])
    if not arr:
        return 0.0
    times = [t for (t, _) in arr]
    i = bisect_right(times, due_dt) - 1
    if i < 0:
        return 0.0
    return float(arr[i][1])


def cumulative_demand_until_due_date(ctx, product_code, due_dt):
    """
    order_layer.orders_sorted_by_due_date[pcode] = [{due_date, qty, ...}, ...]
    """
    ol = ctx["order_layer"]
    orders = ol.get("orders_sorted_by_due_date", {}).get(product_code, [])
    total = 0.0
    for o in orders:
        if o["due_date"] <= due_dt:
            total += float(o["qty"])
        else:
            break
    return total


# ---- Hard constraints ----

def eval_hard_due_date_fulfillment(ctx):
    """
    Σ max(0, required_cum - produced_until) over all orders (qty)
    """
    ol = ctx["order_layer"]
    v = 0.0
    for pcode, orders in ol.get("orders_sorted_by_due_date", {}).items():
        cum_req = 0.0
        for o in orders:
            cum_req += float(o["qty"])
            prod = produced_until(ctx, pcode, o["due_date"])
            v += max(0.0, cum_req - prod)
    return v


def eval_hard_resource_role_assigned(ctx):
    idx = ctx["idx"]
    state = ctx["state"]
    v = 0
    for lot in state.get("lots", []):
        roles = idx.get("resource_roles_by_process", {}).get(lot["process_code"], [])
        assigned = lot.get("assigned_resources", {}) or {}
        for r in roles:
            if r.get("required", True):
                role_code = r["role_code"]
                if not assigned.get(role_code):
                    v += 1
    return v


def eval_hard_time_bucket_valid(ctx):
    tl = ctx["time_layer"]
    state = ctx["state"]
    time_buckets = tl.get("time_buckets", [])
    v = 0
    for lot in state.get("lots", []):
        try:
            if lot.get("time_bucket_id"):
                if tl.get("bucket_by_id", {}).get(lot["time_bucket_id"]) is None:
                    v += 1
            else:
                _ = datetime_to_bucket_id(lot["process_start_time"], time_buckets)
        except Exception:
            v += 1
    return v


def eval_hard_no_holiday_work(ctx):
    tl = ctx["time_layer"]
    state = ctx["state"]
    idx = ctx["idx"]
    v_hours = 0.0

    for lot in state.get("lots", []):
        d = lot["process_start_time"].date()
        ds = tl.get("day_schedule_by_date", {}).get(d)
        if ds and ds.get("is_holiday"):
            # same duration calc as usage builder (approx)
            pcode = lot["product_code"]
            steps = idx["process_steps_by_product"].get(pcode, [])
            step = find_step(steps, lot["process_code"])
            if not step:
                continue

            base_qty = float(step.get("base_qty", 1) or 1)
            cycle_sec = float(step.get("cycle_time_sec", 0) or 0)
            setup_min = float(step.get("setup_time_min", 0) or 0)

            qty = float(lot["qty"])
            batch_count = ceil_div(int(qty), int(base_qty))
            total_sec = (batch_count * cycle_sec) + (setup_min * 60.0)
            v_hours += total_sec / 3600.0

    return v_hours


def eval_hard_compat_machine_mold_process(ctx):
    idx = ctx["idx"]
    state = ctx["state"]
    pairs = idx.get("machine_mold_pairs", {})
    v = 0
    for lot in state.get("lots", []):
        assigned = lot.get("assigned_resources", {}) or {}
        mid = assigned.get("machine")
        mold = assigned.get("mold")
        pr = lot.get("process_code")
        if not mid or not mold:
            continue
        ok = pairs.get((mid, mold, pr), False) or pairs.get((mid, mold, None), False)
        if not ok:
            v += 1
    return v


def eval_hard_compat_product_mold(ctx):
    idx = ctx["idx"]
    state = ctx["state"]
    pm = idx.get("product_molds", {})
    v = 0
    for lot in state.get("lots", []):
        pcode = lot["product_code"]
        pr = lot.get("process_code")
        assigned = lot.get("assigned_resources", {}) or {}
        mold = assigned.get("mold")
        if not mold:
            continue
        allowed = pm.get((pcode, pr))
        if allowed is not None and mold not in allowed:
            v += 1
    return v


def eval_hard_capacity_bucket(ctx):
    rl = ctx["resource_layer"]
    cap = rl.get("capacity_bucket", {})
    use = rl.get("usage_bucket", {})
    v_hours = 0.0

    for type_code, by_res in use.items():
        for rid, by_bucket in by_res.items():
            cap_by_bucket = cap.get(type_code, {}).get(rid, {})
            for b, u in by_bucket.items():
                c = float(cap_by_bucket.get(b, 0.0))
                v_hours += max(0.0, float(u) - c)

    return v_hours


def eval_hard_capacity_segment(ctx):
    """
    usage_shift ve capacity_shift segment_code ile tutulur.
    """
    rl = ctx["resource_layer"]
    capS = rl.get("capacity_shift", {})
    useS = rl.get("usage_shift", {})
    v_hours = 0.0

    for type_code, by_res in capS.items():
        for rid, by_date in by_res.items():
            for d, by_seg in by_date.items():
                for seg_code, cap_hours in by_seg.items():
                    u = float(useS.get(type_code, {}).get(rid, {}).get(d, {}).get(seg_code, 0.0))
                    v_hours += max(0.0, u - float(cap_hours))

    return v_hours


# ---- Soft terms (objective components) ----

def eval_soft_mold_change_total(ctx):
    rl = ctx["resource_layer"]
    ch = rl.get("changeovers", {}).get("machine", {}).get("mold", {})
    total = 0
    for machine_id, by_date in ch.items():
        for d, by_seg in by_date.items():
            for seg_code, events in by_seg.items():
                total += len(events)
    return total


def eval_soft_night_mold_change(ctx, night_constraint_code="NO_MOLD_CHANGE_AT_NIGHT"):
    rl = ctx["resource_layer"]
    tl = ctx["time_layer"]

    ch = rl.get("changeovers", {}).get("machine", {}).get("mold", {})
    seg_constraints = tl.get("segment_constraints_by_segment_code", {})

    v = 0
    for machine_id, by_date in ch.items():
        for d, by_seg in by_date.items():
            for seg_code, events in by_seg.items():
                codes = seg_constraints.get(seg_code, [])
                if night_constraint_code in codes:
                    v += len(events)
    return v


def eval_soft_inventory_low(ctx):
    # holding proxy: sum of positive closing stock
    pl = ctx["product_layer"]
    closing = pl.get("closing_stock", {})
    v = 0.0
    for pcode, by_bucket in closing.items():
        for b, qty in by_bucket.items():
            v += max(0.0, float(qty))
    return v


def eval_soft_machine_count_low(ctx, eps=1e-9):
    rl = ctx["resource_layer"]
    useB = rl.get("usage_bucket", {}).get("machine", {})
    used = 0
    for mid, by_bucket in useB.items():
        if sum(float(x) for x in by_bucket.values()) > eps:
            used += 1
    return used


# ---- Main evaluator (hard + soft) ----

def evaluate_constraints(ctx):
    """
    Returns:
      {
        "feasible": bool,
        "hard": {"total": number, "items": [...]},
        "soft": {"total_cost": number, "terms": [...]}
      }
    """
    scenario = ctx.get("scenario", {}) or {}
    weights = scenario.get("weights", {}) or {}
    toggles = scenario.get("toggles", {}) or {}

    hard_items = []

    def add_hard(code, value, unit):
        if toggles.get(code, True):
            hard_items.append({"code": code, "value": value, "unit": unit})

    add_hard("HARD_DUE_DATE_FULFILLMENT",         eval_hard_due_date_fulfillment(ctx), "qty")
    add_hard("HARD_RESOURCE_ROLE_ASSIGNED",       eval_hard_resource_role_assigned(ctx), "count")
    add_hard("HARD_TIME_BUCKET_VALID",            eval_hard_time_bucket_valid(ctx), "count")
    add_hard("HARD_NO_HOLIDAY_WORK",              eval_hard_no_holiday_work(ctx), "hours")
    add_hard("HARD_COMPAT_MACHINE_MOLD_PROCESS",  eval_hard_compat_machine_mold_process(ctx), "count")
    add_hard("HARD_COMPAT_PRODUCT_MOLD",          eval_hard_compat_product_mold(ctx), "count")
    add_hard("HARD_CAPACITY_BUCKET",              eval_hard_capacity_bucket(ctx), "hours")
    add_hard("HARD_CAPACITY_SEGMENT",             eval_hard_capacity_segment(ctx), "hours")

    hard_total = sum(float(it["value"]) for it in hard_items)
    feasible = (hard_total <= 0.0)

    soft_terms = []

    def add_soft(code, value, weight_key, unit):
        if toggles.get(code, True):
            w = float(weights.get(weight_key, 1.0))
            soft_terms.append({
                "code": code,
                "value": float(value),
                "unit": unit,
                "weight": w,
                "cost": w * float(value)
            })

    # Objectives (senin öncelikler)
    add_soft("SOFT_MOLD_CHANGE_MINIMIZE",  eval_soft_mold_change_total(ctx), "w_mold_change", "count")
    add_soft("SOFT_NIGHT_MOLD_CHANGE",     eval_soft_night_mold_change(ctx), "w_night_mold_change", "count")
    add_soft("SOFT_INVENTORY_LOW",         eval_soft_inventory_low(ctx), "w_inventory", "qty")
    add_soft("SOFT_MACHINE_COUNT_LOW",     eval_soft_machine_count_low(ctx), "w_machine_count", "count")

    soft_total_cost = sum(t["cost"] for t in soft_terms)

    return {
        "feasible": feasible,
        "hard": {"total": hard_total, "items": hard_items},
        "soft": {"total_cost": soft_total_cost, "terms": soft_terms}
    }


def total_cost(ctx):
    res = evaluate_constraints(ctx)
    if not res["feasible"]:
        return INF, res
    return res["soft"]["total_cost"], res


# ============================================================
# 15) Example scenario config (weights/toggles)
# ============================================================

def example_scenario():
    return {
        "weights": {
            "w_mold_change": 10.0,
            "w_night_mold_change": 100.0,
            "w_inventory": 1.0,
            "w_machine_count": 5.0
        },
        "toggles": {
            # hard constraints
            "HARD_DUE_DATE_FULFILLMENT": True,
            "HARD_RESOURCE_ROLE_ASSIGNED": True,
            "HARD_TIME_BUCKET_VALID": True,
            "HARD_NO_HOLIDAY_WORK": True,
            "HARD_COMPAT_MACHINE_MOLD_PROCESS": True,
            "HARD_COMPAT_PRODUCT_MOLD": True,
            "HARD_CAPACITY_BUCKET": True,
            "HARD_CAPACITY_SEGMENT": True,

            # soft terms
            "SOFT_MOLD_CHANGE_MINIMIZE": True,
            "SOFT_NIGHT_MOLD_CHANGE": True,
            "SOFT_INVENTORY_LOW": True,
            "SOFT_MACHINE_COUNT_LOW": True,
        }
    }

# ============================================================
# 16) Constraint Catalog (constraint_code sözlüğü) (NEW)
# ============================================================

def build_constraint_catalog(ctx):
    """
    Returns:
      catalog: dict[str, dict]
        {
          "HARD_DUE_DATE_FULFILLMENT": {...},
          "SOFT_NIGHT_MOLD_CHANGE": {...},
          "TAG::NO_MOLD_CHANGE_AT_NIGHT": {...},
          "ROLE::machine": {...},
          "RULE::MACHINE_MOLD_PAIR": {...},
          ...
        }

    Amaç:
      - tüm constraint kodlarını tek sözlükte topla
      - hard/soft/tag/role/rule ayrımı yap
      - evaluator/weight_key/depends_on_tags gibi meta verileri ekle
    """
    catalog = {}

    def add(
        code: str,
        kind: str,              # "hard" | "soft" | "tag" | "role" | "rule"
        desc: str = "",
        unit: str = "",
        origin: str = "",
        evaluator_name: str = "",
        weight_key: str = "",
        depends_on_tags=None,   # list[str]
    ):
        if depends_on_tags is None:
            depends_on_tags = []

        # idempotent: var ise merge et (origin ekle, desc boşsa doldur)
        cur = catalog.get(code, {})
        merged = {
            "code": code,
            "kind": kind,
            "desc": desc or cur.get("desc", ""),
            "unit": unit or cur.get("unit", ""),
            "origin": (cur.get("origin", "") + (" | " if cur.get("origin") and origin else "") + origin).strip(),
            "evaluator": evaluator_name or cur.get("evaluator", ""),
            "weight_key": weight_key or cur.get("weight_key", ""),
            "depends_on_tags": sorted(set((cur.get("depends_on_tags") or []) + list(depends_on_tags))),
        }
        catalog[code] = merged

    # --------------------------------------------------------
    # A) Built-in evaluator constraints (hard/soft)
    # --------------------------------------------------------
    add(
        "HARD_DUE_DATE_FULFILLMENT",
        kind="hard",
        desc="Kümülatif termin talebi, termin tarihine kadar üretilmiş olmalı (qty shortfall)",
        unit="qty",
        origin="evaluator",
        evaluator_name="eval_hard_due_date_fulfillment",
    )
    add(
        "HARD_RESOURCE_ROLE_ASSIGNED",
        kind="hard",
        desc="Lot üzerinde gerekli resource role'leri atanmış olmalı (missing count)",
        unit="count",
        origin="evaluator",
        evaluator_name="eval_hard_resource_role_assigned",
    )
    add(
        "HARD_TIME_BUCKET_VALID",
        kind="hard",
        desc="Lot time_bucket_id geçerli olmalı / dt bucket'a map edilebilmeli",
        unit="count",
        origin="evaluator",
        evaluator_name="eval_hard_time_bucket_valid",
    )
    add(
        "HARD_NO_HOLIDAY_WORK",
        kind="hard",
        desc="Tatil gününde çalışma olmamalı (ihlalin saat karşılığı)",
        unit="hours",
        origin="evaluator",
        evaluator_name="eval_hard_no_holiday_work",
    )
    add(
        "HARD_COMPAT_MACHINE_MOLD_PROCESS",
        kind="hard",
        desc="(machine,mold,process) uyumluluğu sağlanmalı",
        unit="count",
        origin="evaluator",
        evaluator_name="eval_hard_compat_machine_mold_process",
    )
    add(
        "HARD_COMPAT_PRODUCT_MOLD",
        kind="hard",
        desc="(product,process) için izinli mold listesi dışına çıkılmamalı",
        unit="count",
        origin="evaluator",
        evaluator_name="eval_hard_compat_product_mold",
    )
    add(
        "HARD_CAPACITY_BUCKET",
        kind="hard",
        desc="Bucket bazlı kapasite aşımı olmamalı (aşım saat)",
        unit="hours",
        origin="evaluator",
        evaluator_name="eval_hard_capacity_bucket",
    )
    add(
        "HARD_CAPACITY_SEGMENT",
        kind="hard",
        desc="Segment bazlı kapasite aşımı olmamalı (aşım saat)",
        unit="hours",
        origin="evaluator",
        evaluator_name="eval_hard_capacity_segment",
    )

    add(
        "SOFT_MOLD_CHANGE_MINIMIZE",
        kind="soft",
        desc="Toplam mold change sayısını minimize et",
        unit="count",
        origin="objective",
        evaluator_name="eval_soft_mold_change_total",
        weight_key="w_mold_change",
    )
    add(
        "SOFT_NIGHT_MOLD_CHANGE",
        kind="soft",
        desc="Gece segmentlerinde mold change minimize et",
        unit="count",
        origin="objective",
        evaluator_name="eval_soft_night_mold_change",
        weight_key="w_night_mold_change",
        depends_on_tags=["NO_MOLD_CHANGE_AT_NIGHT"],  # tag ile bağla
    )
    add(
        "SOFT_INVENTORY_LOW",
        kind="soft",
        desc="Stok (pozitif kapanış) toplamını minimize et (holding proxy)",
        unit="qty",
        origin="objective",
        evaluator_name="eval_soft_inventory_low",
        weight_key="w_inventory",
    )
    add(
        "SOFT_MACHINE_COUNT_LOW",
        kind="soft",
        desc="Kullanılan makine sayısını minimize et (konsolidasyon)",
        unit="count",
        origin="objective",
        evaluator_name="eval_soft_machine_count_low",
        weight_key="w_machine_count",
    )

    # --------------------------------------------------------
    # B) Segment tags discovery (time_layer -> segment constraints)
    # --------------------------------------------------------
    tl = ctx.get("time_layer", {}) or {}
    seg_constraints = tl.get("segment_constraints_by_segment_code", {}) or {}

    for seg_code, codes in seg_constraints.items():
        for c in (codes or []):
            add(
                f"TAG::{c}",
                kind="tag",
                desc=f"Zaman segment etiketi: {c}",
                unit="",
                origin=f"time.segment_constraints (segment_code={seg_code})",
            )

    # --------------------------------------------------------
    # C) Process roles discovery (process constraints -> required roles)
    # --------------------------------------------------------
    idx = ctx.get("idx", {}) or {}
    roles_by_process = idx.get("resource_roles_by_process", {}) or {}

    role_set = set()
    for pr_code, roles in roles_by_process.items():
        for r in (roles or []):
            role_set.add(r.get("role_code"))

    for role_code in sorted(x for x in role_set if x):
        add(
            f"ROLE::{role_code}",
            kind="role",
            desc=f"Process resource role: {role_code}",
            unit="",
            origin="process.constraints -> resource_roles_by_process",
        )

    # --------------------------------------------------------
    # D) Compatibility rule presence (as rule entries)
    # --------------------------------------------------------
    # Bunlar evaluator tarafından kullanılıyor; catalog'da 'rule' olarak görünür.
    add(
        "RULE::MACHINE_MOLD_PAIR",
        kind="rule",
        desc="İzinli (machine_id, mold_code, process_code) kombinasyonları",
        unit="",
        origin="compatibility.machine_mold_pairs",
    )
    add(
        "RULE::PRODUCT_MOLD",
        kind="rule",
        desc="İzinli mold listesi (product_code, process_code) -> allowed_molds",
        unit="",
        origin="compatibility.product_molds",
    )

    ctx["constraint_catalog"] = catalog
    return catalog


def constraint_catalog_to_rows(catalog: dict):
    """
    Catalog'u tablo gibi kullanmak için list[dict] formuna çevirir.
    """
    rows = []
    for code, meta in catalog.items():
        rows.append({
            "code": meta.get("code"),
            "kind": meta.get("kind"),
            "unit": meta.get("unit"),
            "weight_key": meta.get("weight_key"),
            "evaluator": meta.get("evaluator"),
            "depends_on_tags": ",".join(meta.get("depends_on_tags") or []),
            "origin": meta.get("origin"),
            "desc": meta.get("desc"),
        })
    # stabil sıralama: önce hard/soft sonra diğerleri
    order = {"hard": 0, "soft": 1, "tag": 2, "role": 3, "rule": 4}
    rows.sort(key=lambda r: (order.get(r["kind"], 99), r["code"]))
    return rows


def evaluate_constraints_from_catalog(ctx):
    """
    Catalog + scenario toggles/weights kullanarak evaluate yapar.
    Not: tag/role/rule entry'leri evaluate edilmez; sadece bilgi amaçlıdır.
    """
    catalog = ctx.get("constraint_catalog") or build_constraint_catalog(ctx)

    scenario = ctx.get("scenario", {}) or {}
    weights = scenario.get("weights", {}) or {}
    toggles = scenario.get("toggles", {}) or {}

    # evaluator dispatch (string -> function)
    eval_map = {
        "eval_hard_due_date_fulfillment": eval_hard_due_date_fulfillment,
        "eval_hard_resource_role_assigned": eval_hard_resource_role_assigned,
        "eval_hard_time_bucket_valid": eval_hard_time_bucket_valid,
        "eval_hard_no_holiday_work": eval_hard_no_holiday_work,
        "eval_hard_compat_machine_mold_process": eval_hard_compat_machine_mold_process,
        "eval_hard_compat_product_mold": eval_hard_compat_product_mold,
        "eval_hard_capacity_bucket": eval_hard_capacity_bucket,
        "eval_hard_capacity_segment": eval_hard_capacity_segment,

        "eval_soft_mold_change_total": eval_soft_mold_change_total,
        "eval_soft_night_mold_change": eval_soft_night_mold_change,
        "eval_soft_inventory_low": eval_soft_inventory_low,
        "eval_soft_machine_count_low": eval_soft_machine_count_low,
    }

    hard_items = []
    soft_terms = []

    for code, meta in catalog.items():
        kind = meta.get("kind")
        if kind not in ("hard", "soft"):
            continue

        if not toggles.get(code, True):
            continue

        evaluator_name = meta.get("evaluator", "")
        fn = eval_map.get(evaluator_name)
        if not fn:
            # catalog'ta var ama evaluator map'te yoksa atla (psödo)
            continue

        value = fn(ctx)
        unit = meta.get("unit", "")

        if kind == "hard":
            hard_items.append({"code": code, "value": float(value), "unit": unit})
        else:
            w_key = meta.get("weight_key", "")
            w = float(weights.get(w_key, 1.0))
            soft_terms.append({
                "code": code,
                "value": float(value),
                "unit": unit,
                "weight": w,
                "cost": w * float(value)
            })

    hard_total = sum(it["value"] for it in hard_items)
    feasible = (hard_total <= 0.0)
    soft_total_cost = sum(t["cost"] for t in soft_terms)

    return {
        "feasible": feasible,
        "hard": {"total": hard_total, "items": hard_items},
        "soft": {"total_cost": soft_total_cost, "terms": soft_terms},
    }


# İstersen build_evaluation_context sonunda otomatik catalog kur:
#   build_constraint_catalog(ctx)
#
# Örnek kullanım:
#   ctx = build_evaluation_context(problem, state, scenario)
#   catalog = build_constraint_catalog(ctx)
#   rows = constraint_catalog_to_rows(catalog)
#   res = evaluate_constraints_from_catalog(ctx)