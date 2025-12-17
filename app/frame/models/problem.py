# TR: ProblemData/ScenarioConfig/State icin Pydantic veri modelleri.
# EN: Pydantic data models for ProblemData/ScenarioConfig/State.
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProblemMeta(BaseModel):
    problem_code: str
    horizon_type: Optional[str] = None
    base_shift_templates_code: Optional[str] = None


class TimeBucket(BaseModel):
    id: str
    index: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class OrderItem(BaseModel):
    week: str
    qty: float


class OrderGroup(BaseModel):
    product_code: str
    orders: List[OrderItem]


class StockItem(BaseModel):
    product_code: str
    warehouse: str
    qty: float


class ProcessInput(BaseModel):
    material_code: str
    qty_per_output_unit: float
    qty_unit: str
    scrap_factor: float = 0.0


class ProcessStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_by_name=True, validate_by_alias=True)

    step_no: int
    process_code: str
    name: str
    output_material: str
    base_qty: float
    base_qty_unit: Optional[str] = Field(default=None, alias="base_qty_unit")
    base_qty_type: Optional[str] = Field(default=None, alias="base_qty_type")
    yield_factor: float
    setup_time_min: float
    cycle_time_sec: float
    wait_time: float = 0
    wait_unit: str = "DAY"
    inputs: List[ProcessInput] = Field(default_factory=list)
    process_input: List[ProcessInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize(self) -> "ProcessStep":
        # Accept both inputs and process_input naming.
        if not self.inputs and self.process_input:
            self.inputs = self.process_input
        # Accept base_qty_type alias.
        if self.base_qty_unit is None and self.base_qty_type is not None:
            self.base_qty_unit = self.base_qty_type
        return self


class Product(BaseModel):
    code: str
    name: str
    base_unit: str
    weight_per_unit_kg: Optional[float] = None
    process_data: List[ProcessStep]


class Process(BaseModel):
    code: str
    name: str
    default_params: Optional[Dict[str, Any]] = None
    constraints: List[Any] = Field(default_factory=list)


class Machine(BaseModel):
    id: str | int
    name: str
    process_code: str
    shifts: Optional[List[str]] = None
    weekly_capacity: Optional[Dict[str, float]] = None


class Mold(BaseModel):
    code: str
    name: str
    process_code: str
    cavities: Optional[int] = None
    eye: Optional[int] = None
    supported_products: Optional[List[str]] = None
    supported_products_id: Optional[List[int]] = None
    compatible_machines: Optional[List[str]] = None
    compatible_machines_id: Optional[List[int]] = None


class Resources(BaseModel):
    machine: List[Machine] = Field(default_factory=list)
    mold: List[Mold] = Field(default_factory=list)


class MachineMoldPair(BaseModel):
    machine_id: str | int
    mold_code: str
    process_code: str


class ProductMold(BaseModel):
    product_code: str
    process_code: str
    allowed_molds: List[str]


class Compatibility(BaseModel):
    machine_mold_pairs: List[MachineMoldPair] = Field(default_factory=list)
    product_molds: List[ProductMold] = Field(default_factory=list)


class ShiftSegment(BaseModel):
    code: str
    start: str
    end: str
    constraints: List[str] = Field(default_factory=list)


class ShiftTemplate(BaseModel):
    code: str
    name: Optional[str] = None
    segments: List[ShiftSegment]


class WorkCalendarEntry(BaseModel):
    date: date
    shift_templates_code: str
    holiday: bool = False


class WorkCalendar(BaseModel):
    entries: List[WorkCalendarEntry] = Field(default_factory=list)


class ProblemData(BaseModel):
    problem_meta: ProblemMeta
    time_buckets: List[TimeBucket]
    orders: List[OrderGroup] = Field(default_factory=list)
    stocks: List[StockItem] = Field(default_factory=list)
    products: List[Product] = Field(default_factory=list)
    processes: List[Process] = Field(default_factory=list)
    resources: Resources = Field(default_factory=Resources)
    shift_templates: List[ShiftTemplate] = Field(default_factory=list)
    work_calendar: List[WorkCalendarEntry] = Field(default_factory=list)
    compatibility: Compatibility = Field(default_factory=Compatibility)


class ScenarioMeta(BaseModel):
    name: str
    description: Optional[str] = None


class ScenarioConstraint(BaseModel):
    code: str
    type: str
    active: bool = True
    weight: Optional[float] = None
    time_scope: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    shift_based: Optional[bool] = None


class ScenarioConfig(BaseModel):
    meta: ScenarioMeta
    constraints: List[ScenarioConstraint] = Field(default_factory=list)


class PlanResource(BaseModel):
    type: str
    id: str | int


class PlanItem(BaseModel):
    lot_id: Optional[str] = None
    product_code: str
    process_code: str
    week: Optional[str] = None
    qty: float
    qty_type: Optional[str] = None
    setup_start_time: Optional[datetime] = None
    setup_end_time: Optional[datetime] = None
    process_start_time: Optional[datetime] = None
    process_end_time: Optional[datetime] = None
    resources: List[PlanResource] = Field(default_factory=list)


class LotInventory(BaseModel):
    product_code: str
    time_bucket_id: str | None = None
    week: Optional[str] = None
    opening_stock: float
    production_qty: float
    demand: float
    closing_stock: float


class StateMeta(BaseModel):
    iteration: Optional[int] = None
    order_fulfillment_rate: Optional[float] = None
    makespan: Optional[float] = None


class State(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_by_name=True, validate_by_alias=True)

    meta: Optional[StateMeta] = None
    lots: List[PlanItem] = Field(default_factory=list)
    inventory: List[LotInventory] = Field(default_factory=list)
    plan: List[PlanItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_plan(self) -> "State":
        if not self.lots and self.plan:
            self.lots = self.plan
        return self


class ProblemFrame(BaseModel):
    problemData: ProblemData
    scenarioConfig: ScenarioConfig
    state: State
