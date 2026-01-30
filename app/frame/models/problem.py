# TR: ProblemData/ScenarioConfig/State icin Pydantic veri modelleri.
# EN: Pydantic data models for ProblemData/ScenarioConfig/State.
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from app.frame.ingest.normalizer import weight_key_for_code, code_mapping


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
    order_id: Optional[str] = None
    time_bucket_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("time_bucket_id", "week"))
    due_date: Optional[datetime] = None
    qty: float
    qty_type: Optional[str] = None


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
    capacity_by_bucket: Optional[Dict[str, float]] = Field(default_factory=dict, validation_alias=AliasChoices("capacity_by_bucket", "weekly_capacity"))
    shift_templates_code: Optional[str] = None


class Mold(BaseModel):
    code: str
    name: str
    process_code: str
    cavities: Optional[int] = None
    eye: Optional[int] = None
    supported_products: Optional[List[str]] = None
    supported_products_id: Optional[List[int | str]] = None
    compatible_machines: Optional[List[int]] = None
    compatible_machines_id: Optional[List[int]] = None

    @model_validator(mode="before")
    def ensure_code(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if values is None:
            return values
        if not values.get("code") and values.get("id") is not None:
            values["code"] = str(values["id"])
        return values


class Resources(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    machines: List[Machine] = Field(default_factory=list, validation_alias=AliasChoices("machines", "machine"))
    molds: List[Mold] = Field(default_factory=list, validation_alias=AliasChoices("molds", "mold"))

    @model_validator(mode="before")
    def fold_legacy_keys(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if values is None:
            return values
        # keep any additional resource groups as-is (extra="allow")
        return values


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
    weights: Dict[str, float] = Field(default_factory=dict)
    toggles: Dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="before")
    def fill_from_constraints(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if values is None:
            return values
        constraints = values.get("constraints", []) or []
        toggles = values.get("toggles", {}) or {}
        weights = values.get("weights", {}) or {}
        for c in constraints:
            code = c.get("code")
            if not code:
                continue
            mapped = code_mapping().get(code, code)
            if c.get("type") == "hard":
                toggles.setdefault(mapped, c.get("active", True))
            else:
                toggles.setdefault(mapped, c.get("active", True))
                if c.get("weight") is not None:
                    wk = weight_key_for_code(mapped) or f"w_{mapped.lower()}"
                    weights.setdefault(wk, c.get("weight"))
        values["toggles"] = toggles
        values["weights"] = weights
        return values


class PlanResource(BaseModel):
    type: str
    id: str | int


class PlanItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_by_name=True, validate_by_alias=True)

    lot_id: Optional[str] = None
    product_code: str
    process_code: str
    time_bucket_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("time_bucket_id", "week"))
    qty: float
    qty_type: Optional[str] = None
    setup_start_time: Optional[datetime] = None
    setup_end_time: Optional[datetime] = None
    process_start_time: Optional[datetime] = None
    process_end_time: Optional[datetime] = None
    resources: List[PlanResource] = Field(default_factory=list)
    assigned_resources: Dict[str, str | int] = Field(default_factory=dict)
    segment_code: Optional[str] = None

    @model_validator(mode="after")
    def normalize(self) -> "PlanItem":
        if not self.assigned_resources and self.resources:
            assigned: Dict[str, str | int] = {}
            for res in self.resources:
                assigned[res.type] = res.id
            self.assigned_resources = assigned

        # normalize machine id to int when numeric
        if self.assigned_resources.get("machine") is not None:
            m = self.assigned_resources["machine"]
            if isinstance(m, str) and m.isdigit():
                self.assigned_resources["machine"] = int(m)
        return self


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
    lots: List[PlanItem] = Field(default_factory=list, validation_alias=AliasChoices("lots", "plan"))
    inventory_summary: List[LotInventory] = Field(default_factory=list, validation_alias=AliasChoices("inventory_summary", "inventory"))

    @model_validator(mode="before")
    def split_inventory_and_plan(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if values is None:
            return values

        lots_val = values.get("lots") or values.get("plan") or []
        if isinstance(lots_val, list) and lots_val:
            sample = lots_val[0]
            if isinstance(sample, dict) and "opening_stock" in sample:
                # treat provided lots as inventory summary
                values["inventory_summary"] = lots_val
                values["lots"] = values.get("plan") or []
            else:
                values["lots"] = lots_val

        if "inventory_summary" not in values and "inventory" in values:
            values["inventory_summary"] = values["inventory"]

        return values

    @model_validator(mode="after")
    def normalize_plan(self) -> "State":
        return self


class ProblemFrame(BaseModel):
    problemData: ProblemData
    scenarioConfig: ScenarioConfig
    state: State
