"""Crop Risk API — the notebook's analysis behind HTTP.

    fastapi dev api/main.py      (development, reload)
    fastapi run api/main.py      (production)

The Streamlit app this replaces took sliders and printed a report; the same
report is now a JSON document any client can call.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from api import schemas
from engine import (
    ASSUMPTIONS,
    DECLINE_THRESHOLD,
    FEATURE_LABELS,
    KY_WHEAT,
    SOIL_POINTS,
    SPLIT_YEAR,
    STAGE_LABELS,
    Engine,
    risk_band,
)

API_VERSION = "1.0.0"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One fit per container: the CSV is read and the models are trained once,
    # not per request.
    app.state.engine = Engine()
    yield


app = FastAPI(
    title="Egypt Crop Risk API",
    version=API_VERSION,
    lifespan=lifespan,
    summary="Farmer-level crop risk for Egyptian crops, built on FAOSTAT yields.",
    description=(
        "Replaces the Streamlit risk app with an HTTP service.\n\n"
        "**What it answers**\n\n"
        "* *What is next year's yield?* The naive baseline (next year = last year) is "
        "served because it beat linear regression and random forest on 2016-2024.\n"
        "* *How likely is a drop above 10 %?* A logistic regression over three "
        "scale-free changes (`ch1`, `ch2`, `gap`), explained per request with exact "
        "linear SHAP values.\n"
        "* *What is the farmer's total risk?* Model probability + FAO water-loss rule "
        "(`Ky x deficit`) + soil points, capped at 100.\n\n"
        "**Honesty** — `GET /v1/models/metrics` returns the out-of-sample scores, and "
        "every report carries the assumptions behind the rule-based part."
    ),
    contact={"name": "Mohamed Sherif", "email": "mohamed.sherif.data@gmail.com"},
    license_info={"name": "MIT"},
    openapi_tags=[
        {"name": "meta", "description": "Service, dataset and rule facts."},
        {"name": "crops", "description": "Catalogue and historical yields."},
        {"name": "risk", "description": "Farmer report and next-year ranking."},
        {"name": "models", "description": "Out-of-sample performance."},
    ],
)


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    """Never let a stale page or a stale number survive a redeploy."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    elif path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


EngineDep = Annotated[Engine, Depends(get_engine)]


def resolve_crop(engine: Engine, name: str) -> str:
    resolved = engine.resolve(name)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown crop '{name}'. GET /v1/crops lists the {len(engine.crops())} crops.",
        )
    return resolved


# ------------------------------------------------------------------ routers
meta_router = APIRouter(prefix="/v1", tags=["meta"], responses={404: {"description": "Not found"}})
crops_router = APIRouter(prefix="/v1/crops", tags=["crops"])
risk_router = APIRouter(prefix="/v1/risk", tags=["risk"])
models_router = APIRouter(prefix="/v1/models", tags=["models"])


@meta_router.get("/health", response_model=schemas.HealthResponse, summary="Liveness")
def health(engine: EngineDep) -> schemas.HealthResponse:
    crops = engine.crops()
    return schemas.HealthResponse(
        status="ok",
        version=API_VERSION,
        crops=len(crops),
        years=f"{min(crop.first_year for crop in crops)}-{max(crop.last_year for crop in crops)}",
    )


@meta_router.get("/meta", response_model=schemas.MetaResponse, summary="Dataset and rule facts")
def meta(engine: EngineDep) -> schemas.MetaResponse:
    crops = engine.crops()
    return schemas.MetaResponse(
        records=engine.records,
        crops=len(crops),
        first_year=min(crop.first_year for crop in crops),
        last_year=max(crop.last_year for crop in crops),
        target_year=engine.target_year,
        split_year=SPLIT_YEAR,
        decline_threshold=DECLINE_THRESHOLD,
        soils=list(SOIL_POINTS),  # type: ignore[arg-type]
        stages=list(STAGE_LABELS),  # type: ignore[arg-type]
        soil_points=SOIL_POINTS,
        ky_wheat=KY_WHEAT,
        assumptions=ASSUMPTIONS,
    )


@crops_router.get("", response_model=schemas.CropList, summary="List crops")
def list_crops(engine: EngineDep) -> schemas.CropList:
    rows = [
        schemas.CropRow(
            item=crop.item,
            years=crop.years,
            first_year=crop.first_year,
            last_year=crop.last_year,
            latest_yield=crop.latest_yield,
        )
        for crop in sorted(engine.crops(), key=lambda crop: crop.item)
    ]
    return schemas.CropList(count=len(rows), items=rows)


@crops_router.get("/{item:path}/history", response_model=schemas.HistoryResponse, summary="Yield history")
def history(item: str, engine: EngineDep) -> schemas.HistoryResponse:
    crop = resolve_crop(engine, item)
    return schemas.HistoryResponse(
        item=crop,
        points=[schemas.HistoryPoint(**point) for point in engine.history(crop)],
    )


@risk_router.post("/report", response_model=schemas.ReportResponse, summary="Farmer risk report")
def report(payload: schemas.ReportRequest, engine: EngineDep) -> schemas.ReportResponse:
    try:
        body = engine.report(payload.crop, payload.soil, payload.water_percent, payload.stage)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown crop '{payload.crop}'. GET /v1/crops lists the valid names.",
        ) from None
    return schemas.ReportResponse(**body)


@risk_router.get("/ranking", response_model=schemas.RankingResponse, summary="Next-year drop ranking")
def ranking(
    engine: EngineDep,
    limit: Annotated[int, Query(ge=1, le=100, description="Rows to return, worst first.")] = 10,
) -> schemas.RankingResponse:
    rows = engine.ranking(limit)
    return schemas.RankingResponse(
        threshold=DECLINE_THRESHOLD,
        count=len(rows),
        rows=[schemas.RankingRow(**row) for row in rows],
    )


@models_router.get("/metrics", response_model=schemas.MetricsResponse, summary="Out-of-sample scores")
def metrics(engine: EngineDep) -> schemas.MetricsResponse:
    return schemas.MetricsResponse(
        split_year=SPLIT_YEAR,
        test_years=engine.test_years,
        test_rows=engine.test_rows,
        models=[
            schemas.ForecastMetric(model=key, **values)  # type: ignore[arg-type]
            for key, values in engine.forecast_metrics.items()
        ],
        risk=schemas.RiskMetrics(**engine.risk_metrics),
    )


for router in (meta_router, crops_router, risk_router, models_router):
    app.include_router(router)


@app.get("/v1", tags=["meta"], summary="Service index")
def index() -> dict:
    return {
        "service": "Egypt Crop Risk API",
        "version": API_VERSION,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "endpoints": [
            "/v1/health",
            "/v1/meta",
            "/v1/crops",
            "/v1/crops/{item}/history",
            "POST /v1/risk/report",
            "/v1/risk/ranking",
            "/v1/models/metrics",
        ],
        "drivers": list(FEATURE_LABELS),
        "risk_bands": {"low": "0-15", "medium": "15-35", "high": "35-100"},
        "note": "The report's rule-based part (soil points, Ky for non-wheat crops) is assumption-based; see /v1/meta.",
    }


# Mounted last so every explicit route above keeps winning; "/" serves web/index.html.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


__all__ = ["app", "risk_band"]
