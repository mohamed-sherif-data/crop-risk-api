"""Request and response models — the public contract of the service."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Soil = Literal["Loam", "Clay", "Sandy", "Saline"]
Stage = Literal["vegetative", "flowering", "grain_filling", "ripening"]
RiskLevel = Literal["low", "medium", "high"]


# ------------------------------------------------------------------- meta
class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    crops: int
    years: str


class MetaResponse(BaseModel):
    records: int
    crops: int
    first_year: int
    last_year: int
    target_year: int
    split_year: int
    decline_threshold: float
    unit: str = "kg/ha"
    soils: list[Soil]
    stages: list[Stage]
    soil_points: dict[str, float]
    ky_wheat: dict[str, float]
    assumptions: list[str]


# ------------------------------------------------------------------ crops
class CropRow(BaseModel):
    item: str
    years: int
    first_year: int
    last_year: int
    latest_yield: float


class CropList(BaseModel):
    count: int
    items: list[CropRow]


class HistoryPoint(BaseModel):
    year: int
    value: float


class HistoryResponse(BaseModel):
    item: str
    unit: str = "kg/ha"
    points: list[HistoryPoint]


# ----------------------------------------------------------------- report
class ReportRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"crop": "Wheat", "soil": "Sandy", "water_percent": 70, "stage": "flowering"}
            ]
        }
    )

    crop: str = Field(description="Crop name exactly as returned by GET /v1/crops (case-insensitive).")
    soil: Soil = Field(default="Loam", description="Soil type; drives a small risk penalty.")
    water_percent: float = Field(
        default=100,
        ge=0,
        le=100,
        description="Share of the crop's water requirement that was actually received.",
    )
    stage: Stage = Field(
        default="flowering",
        description="Growth stage; only wheat has stage-specific FAO Ky values.",
    )


class Driver(BaseModel):
    feature: str
    label: str
    value: float = Field(description="The crop's current value for this driver.")
    contribution: float = Field(description="SHAP value in log-odds (coef x centred feature).")
    share_pct: float = Field(description="Share of the model's total attribution.")
    direction: Literal["raises", "lowers"]


class ReportInputs(BaseModel):
    soil: Soil
    water_percent: float
    stage: Stage


class ReportResponse(BaseModel):
    crop: str
    last_observed_year: int
    target_year: int
    latest_yield_kg_ha: float
    forecast_kg_ha: float = Field(description="Naive forecast: next year equals last year.")
    expected_yield_kg_ha: float = Field(description="Naive forecast after the water-stress loss.")
    model_risk_pct: float
    water_loss_pct: float
    soil_points: float
    total_score: float = Field(description="model risk + water loss + soil points, capped at 100.")
    risk_level: RiskLevel
    ky: float
    inputs: ReportInputs
    drivers: list[Driver]
    assumptions: list[str]


# ------------------------------------------------------------------- risk
class RankingRow(BaseModel):
    rank: int
    item: str
    probability: float
    band: RiskLevel
    latest_yield: float
    last_change: float


class RankingResponse(BaseModel):
    threshold: float
    count: int
    rows: list[RankingRow]


# ----------------------------------------------------------------- models
class ForecastMetric(BaseModel):
    model: Literal["naive", "linear", "forest"]
    r2: float
    mae_kg_ha: float
    mape_pct: float


class RiskMetrics(BaseModel):
    auc: float
    base_rate: float
    top_decile_rate: float
    lift: float


class MetricsResponse(BaseModel):
    unit: str = "kg/ha"
    split_year: int
    test_years: str
    test_rows: int
    models: list[ForecastMetric]
    risk: RiskMetrics
