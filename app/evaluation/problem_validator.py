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
    machine_ids = {str(m.id) for m in frame.problemData.resources.machine}
    mold_codes = {mold.code for mold in frame.problemData.resources.mold}

    for order_group in frame.problemData.orders:
        if order_group.product_code not in product_codes:
            errors.append(f"orders reference unknown product {order_group.product_code}")
        for order in order_group.orders:
            if order.week not in time_bucket_ids:
                errors.append(f"orders reference unknown time bucket {order.week}")

    for stock in frame.problemData.stocks:
        if stock.product_code not in product_codes:
            errors.append(f"stocks reference unknown product {stock.product_code}")

    for product in frame.problemData.products:
        for step in product.process_data:
            if step.process_code not in process_codes:
                errors.append(f"product {product.code} step {step.step_no} refers to unknown process {step.process_code}")

    for machine in frame.problemData.resources.machine:
        if machine.process_code not in process_codes:
            errors.append(f"machine {machine.id} refers to unknown process {machine.process_code}")

    for mold in frame.problemData.resources.mold:
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
        for res in item.resources:
            if res.type == "machine" and str(res.id) not in machine_ids:
                errors.append(f"plan {item.lot_id or 'n/a'} refers to unknown machine {res.id}")
            if res.type == "mold" and res.id not in mold_codes:
                errors.append(f"plan {item.lot_id or 'n/a'} refers to unknown mold {res.id}")
            if res.type == "mold":
                machine_id = str(next((r.id for r in item.resources if r.type == "machine"), ""))
                triple = (machine_id, res.id, item.process_code)
                if allowed_machine_mold and triple not in allowed_machine_mold:
                    errors.append(f"plan {item.lot_id or 'n/a'} uses incompatible machine/mold/process {triple}")
                if allowed_product_mold and (item.product_code, item.process_code, res.id) not in allowed_product_mold:
                    errors.append(
                        f"plan {item.lot_id or 'n/a'} uses mold {res.id} not allowed for product {item.product_code}"
                    )
        if item.week and item.week not in time_bucket_ids:
            errors.append(f"plan {item.lot_id or 'n/a'} references unknown time bucket {item.week}")
        if item.product_code not in product_codes:
            errors.append(f"plan {item.lot_id or 'n/a'} references unknown product {item.product_code}")
        if item.process_code not in process_codes:
            errors.append(f"plan {item.lot_id or 'n/a'} references unknown process {item.process_code}")

    for plan_item in frame.state.lots:
        _check_plan_item(plan_item)

    for inventory in frame.state.inventory:
        if inventory.product_code not in product_codes:
            errors.append(f"inventory row references unknown product {inventory.product_code}")
        if inventory.week and inventory.week not in time_bucket_ids:
            errors.append(f"inventory row references unknown time bucket {inventory.week}")
        if inventory.time_bucket_id and inventory.time_bucket_id not in time_bucket_ids:
            errors.append(f"inventory row references unknown time bucket {inventory.time_bucket_id}")

    return errors
