CONSTRAINT_CATALOG = {
  # -------------------------
  # HARD (Feasibility)
  # -------------------------
  "HARD_DUE_DATE_FULFILLMENT": {
    "hard": True,
    "desc": "Her ürün için due_date'e kadar üretilen kümülatif miktar, due_date'e kadar olan talebi karşılamalı.",
    "depends_on": [
      "order_layer.orders_by_product",
      "order_layer.production_cumsum_by_product",
      "state.lots(process_end_time, qty)"
    ],
    "source_refs": [
      "order_layer + production_cumsum oluşturma"  #:contentReference[oaicite:5]{index=5}
    ],
    "violation_unit": "qty",
    "compute_violation": """
      for each product_code:
        for each order in orders_sorted_by_due_date[product_code]:
          produced = produced_until(product_code, order.due_date)  # binary search on production_cumsum
          required = cumulative_demand_until_due_date(product_code, order.due_date)  # order qty prefix sum
          v += max(0, required - produced)
    """,
    "aggregation": "sum_over_orders_and_products"
  },

  "HARD_RESOURCE_ROLE_ASSIGNED": {
    "hard": True,
    "desc": "Her lot için prosesin gerektirdiği role'lerin (machine, mold vb.) assigned_resources içinde atanmış olması gerekir.",
    "depends_on": [
      "process_layer.resource_roles_by_process",
      "idx.resource_roles_by_process",
      "state.lots.assigned_resources"
    ],
    "source_refs": [
      "process roles üretimi"  #:contentReference[oaicite:6]{index=6}
    ],
    "violation_unit": "count",
    "compute_violation": """
      for each lot:
        roles = idx.resource_roles_by_process[lot.process_code]
        for role in roles:
          if role.required and not lot.assigned_resources.get(role.role_code):
            v += 1
    """,
    "aggregation": "sum_over_lots"
  },

  "HARD_TIME_BUCKET_VALID": {
    "hard": True,
    "desc": "Lot'un process_start_time (veya verilen time_bucket_id) mutlaka time_buckets içine düşmeli.",
    "depends_on": [
      "time_layer.time_buckets",
      "datetime_to_bucket_id()",
      "state.lots.process_start_time / lots.time_bucket_id"
    ],
    "source_refs": [
      "bucket_of_date cache ve datetime_to_bucket_id yaklaşımı"  #:contentReference[oaicite:7]{index=7}
    ],
    "violation_unit": "count",
    "compute_violation": """
      for each lot:
        try:
          b = lot.time_bucket_id or datetime_to_bucket_id(lot.process_start_time, time_layer.time_buckets)
        except ValueError:
          v += 1
    """,
    "aggregation": "sum_over_lots"
  },

  "HARD_NO_HOLIDAY_WORK": {
    "hard": True,
    "desc": "work_calendar'da holiday=true olan günlerde üretim/lot planlanamaz.",
    "depends_on": [
      "time_layer.day_schedule_by_date[date].is_holiday",
      "state.lots.process_start_time"
    ],
    "source_refs": [
      "day_schedule_by_date ve is_holiday"  #:contentReference[oaicite:8]{index=8}
    ],
    "violation_unit": "hours",
    "compute_violation": """
      for each lot:
        date = lot.process_start_time.date()
        if time_layer.day_schedule_by_date.get(date, {}).get('is_holiday'):
          v += estimated_lot_hours(lot)  # aynı total_hours hesabı (usage builder)
    """,
    "aggregation": "sum_over_lots"
  },

  "HARD_CAPACITY_BUCKET": {
    "hard": True,
    "desc": "Her resource için bucket bazlı kullanım, bucket kapasitesini aşamaz.",
    "depends_on": [
      "resource_layer.capacity_bucket[type][rid][bucket]",
      "resource_layer.usage_bucket[type][rid][bucket]"
    ],
    "source_refs": [
      "capacity_bucket üretimi" ,  #:contentReference[oaicite:9]{index=9}
      "usage_bucket üretimi"      #:contentReference[oaicite:10]{index=10}
    ],
    "violation_unit": "hours",
    "compute_violation": """
      for each type_code, rid, bucket:
        cap = capacity_bucket[type_code][rid][bucket]
        use = usage_bucket[type_code][rid][bucket]
        v += max(0, use - cap)
    """,
    "aggregation": "sum_over_all_resources_and_buckets"
  },

  "HARD_CAPACITY_SEGMENT": {
    "hard": True,
    "desc": "Her resource için gün+segment bazlı kullanım, segment kapasitesini aşamaz.",
    "depends_on": [
      "resource_layer.capacity_shift[type][rid][date][segment_code]",
      "resource_layer.usage_shift[type][rid][date][segment_code]  # kanonik segment",
      "time_layer.segment_of_datetime(dt) (gerekirse lot'tan segment üretmek için)"
    ],
    "source_refs": [
      "capacity_shift segment_code ile tutuluyor" ,  #:contentReference[oaicite:11]{index=11}:contentReference[oaicite:12]{index=12}
      "usage_shift şu an shift_code ile"            #:contentReference[oaicite:13]{index=13}
    ],
    "violation_unit": "hours",
    "compute_violation": """
      for each type_code, rid, date, segment_code:
        cap = capacity_shift[type_code][rid][date][segment_code]
        use = usage_shift[type_code][rid][date].get(segment_code, 0.0)
        v += max(0, use - cap)
    """,
    "aggregation": "sum_over_all_resources_dates_segments",
    "note": "usage_shift anahtarını segment_code yapmazsan bu kısıt hatalı ölçülür."
  },

  "HARD_COMPAT_MACHINE_MOLD_PROCESS": {
    "hard": True,
    "desc": "Lot'ta atanan machine+mold (+process) kombinasyonu izinli olmalı.",
    "depends_on": [
      "idx.machine_mold_pairs[(machine_id, mold_code, process_code)]",
      "state.lots.assigned_resources['machine'/'mold'], lots.process_code"
    ],
    "source_refs": [
      "machine_mold_pairs index"  #:contentReference[oaicite:14]{index=14}
    ],
    "violation_unit": "count",
    "compute_violation": """
      for each lot:
        mid  = lot.assigned_resources.get('machine')
        mold = lot.assigned_resources.get('mold')
        pr   = lot.process_code
        if mid and mold:
          if not idx.machine_mold_pairs.get((mid, mold, pr), False) and not idx.machine_mold_pairs.get((mid, mold, None), False):
            v += 1
    """,
    "aggregation": "sum_over_lots"
  },

  "HARD_COMPAT_PRODUCT_MOLD": {
    "hard": True,
    "desc": "Ürün için (process bazlı) izinli mold seti dışına çıkılamaz.",
    "depends_on": [
      "idx.product_molds[(product_code, process_code)] -> set(allowed_molds)",
      "state.lots.product_code, lots.process_code, lots.assigned_resources['mold']"
    ],
    "source_refs": [
      "product_molds index"  #:contentReference[oaicite:15]{index=15}
    ],
    "violation_unit": "count",
    "compute_violation": """
      for each lot:
        key = (lot.product_code, lot.process_code)
        allowed = idx.product_molds.get(key)
        mold = lot.assigned_resources.get('mold')
        if allowed is not None and mold and mold not in allowed:
          v += 1
    """,
    "aggregation": "sum_over_lots"
  },

  # -------------------------
  # SOFT (Objective terms)
  # -------------------------
  "SOFT_MOLD_CHANGE_MINIMIZE": {
    "hard": False,
    "desc": "Kalıp değişimi sayısını azalt (machine üzerinde mold change event sayısı).",
    "depends_on": [
      "resource_layer.sequence['machine'][rid][date][segment_code]  # kanonik",
      "resource_layer.changeovers['machine']['mold']"
    ],
    "source_refs": [
      "changeover event üretimi"  #:contentReference[oaicite:16]{index=16}:contentReference[oaicite:17]{index=17}
    ],
    "violation_unit": "count",
    "compute_violation": """
      v = 0
      for each machine_id, date, seg_code:
        v += len(changeovers['machine']['mold'][machine_id][date][seg_code])
    """,
    "aggregation": "sum_over_all_machines_dates_segments",
    "weight_key": "w_mold_change"
  },

  "SOFT_SEGMENT_CONSTRAINTS": {
    "hard": False,
    "desc": "Segment üzerinde tanımlı constraint_codes'a göre soft ceza üret (örn: gece kalıp değişimi isteme).",
    "depends_on": [
      "time_layer.segment_constraints_by_segment_code[segment_code] -> [constraint_codes...]",
      "changeover events (mold change) or lot events",
      "time_layer.segment_of_datetime(event_time)"
    ],
    "source_refs": [
      "segment->constraint_codes map" ,  #:contentReference[oaicite:18]{index=18}
      "segment_of_datetime helper"       #:contentReference[oaicite:19]{index=19}
    ],
    "violation_unit": "count",
    "compute_violation": """
      v = 0
      # örnek: NO_MOLD_CHANGE_AT_NIGHT
      for each mold_change_event:
        seg = segment_of_datetime(event.time)
        codes = segment_constraints_by_segment_code.get(seg, [])
        if 'NO_MOLD_CHANGE_AT_NIGHT' in codes:
          v += 1
    """,
    "aggregation": "sum_over_events",
    "weight_key": "w_segment_soft"
  },

  "SOFT_INVENTORY_LOW": {
    "hard": False,
    "desc": "Az stok ile çalışma (bucket kapanış stoklarını minimize et).",
    "depends_on": [
      "product_layer.closing_stock[product][bucket]",
      "product_layer.opening_stock/production_out/demand_by_bucket"
    ],
    "source_refs": [
      "closing_stock akışı"  #:contentReference[oaicite:20]{index=20}
    ],
    "violation_unit": "qty",
    "compute_violation": """
      v = 0
      for each product, bucket:
        v += max(0, closing_stock[product][bucket])  # ister direkt closing, ister target üstü
    """,
    "aggregation": "sum_over_products_buckets",
    "weight_key": "w_inventory"
  },

  "SOFT_MACHINE_COUNT_LOW": {
    "hard": False,
    "desc": "Az makina ile çalışma (kullanılan makine sayısını minimize et).",
    "depends_on": [
      "resource_layer.usage_bucket['machine'][machine_id][bucket]"
    ],
    "source_refs": [
      "usage_bucket oluşumu"  #:contentReference[oaicite:21]{index=21}
    ],
    "violation_unit": "count",
    "compute_violation": """
      used = 0
      for each machine_id:
        if sum(usage_bucket['machine'][machine_id].values()) > 0:
          used += 1
      v = used
    """,
    "aggregation": "scalar",
    "weight_key": "w_machine_count"
  },

  "SOFT_MATERIAL_USAGE_TRACK": {
    "hard": False,
    "desc": "Malzeme tüketimini takip (şimdilik stok/tedarik yoksa sadece rapor veya ceza terimi).",
    "depends_on": [
      "product_layer.material_usage[material][bucket]"
    ],
    "source_refs": [
      "material_usage hesabı"  #:contentReference[oaicite:22]{index=22}
    ],
    "violation_unit": "qty",
    "compute_violation": """
      # opsiyonel: toplam tüketimi minimize etmek veya limit aşımlarını cezalandırmak
      v = sum_over_all(material_usage[mat][b])
    """,
    "aggregation": "sum_over_materials_buckets",
    "weight_key": "w_material_usage_optional"
  }
}