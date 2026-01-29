from bisect import bisect_right
from math import inf

INF = 10**18

# ----------------------------
# Helpers (Order / Produced)
# ----------------------------
def produced_until(ctx, product_code, due_dt):
    """
    order_layer.production_cumsum_by_product[pcode] = [(t1, cum1), (t2, cum2), ...]
    due_dt: datetime (veya date -> datetime'a çevirebilirsin)
    """
    ol = ctx["order_layer"]
    arr = ol.get("production_cumsum_by_product", {}).get(product_code, [])
    if not arr:
        return 0

    # bisect_right by time
    times = [t for (t, _) in arr]
    i = bisect_right(times, due_dt) - 1
    if i < 0:
        return 0
    return arr[i][1]

def cumulative_demand_until_due_date(ctx, product_code, due_dt):
    """
    order_layer.orders_sorted_by_due_date[pcode] = [{due_date, qty, ...}, ...]
    due_dt'e kadar olan talep prefix sum.
    """
    ol = ctx["order_layer"]
    orders = ol.get("orders_sorted_by_due_date", {}).get(product_code, [])
    total = 0
    for o in orders:
        if o["due_date"] <= due_dt:
            total += o["qty"]
        else:
            break
    return total

# ----------------------------
# Canonical segment code (for capacity & night)
# ----------------------------
def get_segment_code_for_time(ctx, dt):
    tl = ctx["time_layer"]
    seg = tl.get("segment_of_datetime", lambda x: None)(dt)
    return seg or "NA"

# ----------------------------
# (Optional) ensure usage_shift is keyed by segment_code
# ----------------------------
def ensure_usage_shift_segment_canonical(ctx):
    """
    Eğer usage_shift zaten segment_code ile tutuluyorsa dokunma.
    Eğer shift_code ile tutuluyorsa, lot bazlı tekrar hesap yapmadan,
    en azından capacity check için 'NA' fallback ile çalıştırırsın.
    
    Tavsiye: build_resource_layer_usages_and_sequences tarafında
    usage_shift/sequence anahtarını segment_code yapman.
    """
    # Bu fonksiyon şimdilik "pasif"; gerçek düzeltme builder'da yapılmalı.
    return

# ----------------------------
# Constraint evaluators
# ----------------------------
def eval_hard_due_date_fulfillment(ctx):
    """
    Σ max(0, required - produced) (qty)
    """
    ol = ctx["order_layer"]
    v = 0
    for pcode, orders in ol.get("orders_sorted_by_due_date", {}).items():
        # due_date sırasıyla prefix demand hesapla
        cum_req = 0
        for o in orders:
            cum_req += o["qty"]
            produced = produced_until(ctx, pcode, o["due_date"])
            v += max(0, cum_req - produced)
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
                # sadece varlığı kontrol
                _ = tl.get("bucket_by_id", {}).get(lot["time_bucket_id"])
                if _ is None:
                    v += 1
            else:
                # datetime_to_bucket_id senin helper'ın
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
            # lot süresini usage builder ile aynı şekilde hesapla (yaklaşım)
            pcode = lot["product_code"]
            steps = idx["process_steps_by_product"][pcode]
            step = find_step(steps, lot["process_code"])
            if not step:
                continue
            base_qty  = step.get("base_qty", 1) or 1
            cycle_sec = step.get("cycle_time_sec", 0) or 0
            setup_min = step.get("setup_time_min", 0) or 0

            qty = lot["qty"]
            batch_count = ceil_div(int(qty), int(base_qty))
            total_sec = (batch_count * cycle_sec) + (setup_min * 60)
            v_hours += total_sec / 3600.0

    return v_hours

def eval_hard_compat_machine_mold_process(ctx):
    idx = ctx["idx"]
    state = ctx["state"]
    pairs = idx.get("machine_mold_pairs", {})
    v = 0
    for lot in state.get("lots", []):
        assigned = lot.get("assigned_resources", {}) or {}
        mid  = assigned.get("machine")
        mold = assigned.get("mold")
        pr   = lot.get("process_code")
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
        pr = lot["process_code"]
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
    v_hours = 0.0
    cap = rl.get("capacity_bucket", {})
    use = rl.get("usage_bucket", {})
    for type_code, by_res in use.items():
        for rid, by_bucket in by_res.items():
            cap_by_bucket = cap.get(type_code, {}).get(rid, {})
            for b, u in by_bucket.items():
                c = float(cap_by_bucket.get(b, 0.0))
                v_hours += max(0.0, float(u) - c)
    return v_hours

def eval_hard_capacity_segment(ctx):
    """
    NOTE: bunun doğru çalışması için usage_shift'in segment_code ile tutulması önerilir.
    """
    ensure_usage_shift_segment_canonical(ctx)

    rl = ctx["resource_layer"]
    v_hours = 0.0
    capS = rl.get("capacity_shift", {})
    useS = rl.get("usage_shift", {})

    for type_code, by_res in capS.items():
        for rid, by_date in by_res.items():
            for d, by_seg in by_date.items():
                for seg_code, cap_hours in by_seg.items():
                    u = float(useS.get(type_code, {}).get(rid, {}).get(d, {}).get(seg_code, 0.0))
                    v_hours += max(0.0, u - float(cap_hours))
    return v_hours

# ----------------------------
# Soft KPI / penalties
# ----------------------------
def eval_soft_mold_change_total(ctx):
    rl = ctx["resource_layer"]
    ch = rl.get("changeovers", {}).get("machine", {}).get("mold", {})
    total = 0
    for machine_id, by_date in ch.items():
        for d, by_seg in by_date.items():
            for seg, events in by_seg.items():
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

def eval_soft_inventory_low(ctx, mode="sum_closing"):
    """
    mode:
      - sum_closing: toplam kapanış stok (holding proxy)
      - above_target: hedef üstünü cezalandır (target_min/target_max vb. eklenirse)
    """
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


# ----------------------------
# Main evaluator
# ----------------------------
def evaluate_constraints(ctx):
    """
    Returns:
      {
        "feasible": bool,
        "hard": {
          "total": number,
          "items": [{"code":..., "value":..., "unit":...}, ...]
        },
        "soft": {
          "total_cost": number,
          "terms": [{"code":..., "value":..., "weight":..., "cost":...}, ...]
        }
      }
    """
    scenario = ctx.get("scenario", {}) or {}
    weights = scenario.get("weights", {}) or {}
    toggles = scenario.get("toggles", {}) or {}  # hard/soft enable flags

    # -------- Hard constraints --------
    hard_items = []

    def add_hard(code, value, unit):
        if toggles.get(code, True):  # default enabled
            hard_items.append({"code": code, "value": value, "unit": unit})

    add_hard("HARD_DUE_DATE_FULFILLMENT",      eval_hard_due_date_fulfillment(ctx), "qty")
    add_hard("HARD_RESOURCE_ROLE_ASSIGNED",    eval_hard_resource_role_assigned(ctx), "count")
    add_hard("HARD_TIME_BUCKET_VALID",         eval_hard_time_bucket_valid(ctx), "count")
    add_hard("HARD_NO_HOLIDAY_WORK",           eval_hard_no_holiday_work(ctx), "hours")
    add_hard("HARD_COMPAT_MACHINE_MOLD_PROCESS", eval_hard_compat_machine_mold_process(ctx), "count")
    add_hard("HARD_COMPAT_PRODUCT_MOLD",       eval_hard_compat_product_mold(ctx), "count")
    add_hard("HARD_CAPACITY_BUCKET",           eval_hard_capacity_bucket(ctx), "hours")
    add_hard("HARD_CAPACITY_SEGMENT",          eval_hard_capacity_segment(ctx), "hours")

    hard_total = 0.0
    for it in hard_items:
        hard_total += float(it["value"])

    feasible = (hard_total <= 0.0)

    # -------- Soft terms --------
    soft_terms = []

    def add_soft(code, value, weight_key, unit):
        if toggles.get(code, True):
            w = float(weights.get(weight_key, 1.0))
            soft_terms.append({"code": code, "value": value, "unit": unit, "weight": w, "cost": w * float(value)})

    # senin hedefler
    add_soft("SOFT_MOLD_CHANGE_MINIMIZE",  eval_soft_mold_change_total(ctx), "w_mold_change", "count")
    add_soft("SOFT_NIGHT_MOLD_CHANGE",     eval_soft_night_mold_change(ctx), "w_night_mold_change", "count")
    add_soft("SOFT_INVENTORY_LOW",         eval_soft_inventory_low(ctx), "w_inventory", "qty")
    add_soft("SOFT_MACHINE_COUNT_LOW",     eval_soft_machine_count_low(ctx), "w_machine_count", "count")

    soft_total_cost = sum(t["cost"] for t in soft_terms)

    return {
        "feasible": feasible,
        "hard": {
            "total": hard_total,
            "items": hard_items
        },
        "soft": {
            "total_cost": soft_total_cost,
            "terms": soft_terms
        }
    }


# ----------------------------
# Optional: total cost wrapper
# ----------------------------
def total_cost(ctx):
    res = evaluate_constraints(ctx)
    if not res["feasible"]:
        # Hard ihlalleri çözümü "geçersiz" yapar
        return INF, res
    return res["soft"]["total_cost"], res