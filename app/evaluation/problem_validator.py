# TR: Referans ve tutarlilik kontrollerini yapar.
# EN: Runs reference and consistency checks.
from __future__ import annotations

from typing import List

from app.frame.models.problem import PlanItem, ProblemFrame


def validate_references(frame: ProblemFrame) -> List[str]:
    errors: List[str] = []

    process_codes = {p.code for p in frame.problemData.processes}
    product_codes = {p.code for p in frame.problemData.products}
    time_bucket_ids = {tb.id for tb in frame.problemData.time_buckets}
    machine_ids = {str(m.id) for m in frame.problemData.resources.machines}
    mold_codes = {mold.code for mold in frame.problemData.resources.molds}

    for order_group in frame.problemData.orders:
        if order_group.product_code not in product_codes:
            errors.append(f"orders reference unknown product {order_group.product_code}")
        for order in order_group.orders:
            if order.time_bucket_id and order.time_bucket_id not in time_bucket_ids:
                errors.append(f"orders reference unknown time bucket {order.time_bucket_id}")
            if not order.time_bucket_id and not order.due_date:
                errors.append(f"order {order.order_id or '?'} missing time_bucket_id or due_date")

    for stock in frame.problemData.stocks:
        if stock.product_code not in product_codes:
            errors.append(f"stocks reference unknown product {stock.product_code}")

    for product in frame.problemData.products:
        for step in product.process_data:
            if step.process_code not in process_codes:
                errors.append(f"product {product.code} step {step.step_no} refers to unknown process {step.process_code}")

    for machine in frame.problemData.resources.machines:
        if machine.process_code not in process_codes:
            errors.append(f"machine {machine.id} refers to unknown process {machine.process_code}")

    for mold in frame.problemData.resources.molds:
        if mold.process_code not in process_codes:
            errors.append(f"mold {mold.code} refers to unknown process {mold.process_code}")

    allowed_machine_mold = {
        (str(pair.machine_id), pair.mold_code, pair.process_code)
        for pair in frame.problemData.compatibility.machine_mold_pairs
    }
    allowed_product_mold = {
        (pm.product_code, pm.process_code, mold)
        for pm in frame.problemData.compatibility.product_molds
        for mold in pm.allowed_molds
    }

    def _check_plan_item(item: PlanItem) -> None:
        assigned = item.assigned_resources or {}
        machine_id = assigned.get("machine")
        mold_id = assigned.get("mold")

        if machine_id is not None and str(machine_id) not in machine_ids:
            errors.append(f"plan {item.lot_id or 'n/a'} refers to unknown machine {machine_id}")
        if mold_id is not None and mold_id not in mold_codes:
            errors.append(f"plan {item.lot_id or 'n/a'} refers to unknown mold {mold_id}")

        if mold_id is not None:
            triple = (str(machine_id), mold_id, item.process_code)
            if allowed_machine_mold and triple not in allowed_machine_mold:
                errors.append(f"plan {item.lot_id or 'n/a'} uses incompatible machine/mold/process {triple}")
            if allowed_product_mold and (item.product_code, item.process_code, mold_id) not in allowed_product_mold:
                errors.append(
                    f"plan {item.lot_id or 'n/a'} uses mold {mold_id} not allowed for product {item.product_code}"
                )

        if item.time_bucket_id and item.time_bucket_id not in time_bucket_ids:
            errors.append(f"plan {item.lot_id or 'n/a'} references unknown time bucket {item.time_bucket_id}")
        if item.product_code not in product_codes:
            errors.append(f"plan {item.lot_id or 'n/a'} references unknown product {item.product_code}")
        if item.process_code not in process_codes:
            errors.append(f"plan {item.lot_id or 'n/a'} references unknown process {item.process_code}")

    for plan_item in frame.state.lots:
        _check_plan_item(plan_item)

    for inventory in frame.state.inventory_summary:
        if inventory.product_code not in product_codes:
            errors.append(f"inventory row references unknown product {inventory.product_code}")
        if inventory.week and inventory.week not in time_bucket_ids:
            errors.append(f"inventory row references unknown time bucket {inventory.week}")
        if inventory.time_bucket_id and inventory.time_bucket_id not in time_bucket_ids:
            errors.append(f"inventory row references unknown time bucket {inventory.time_bucket_id}")

    return errors
