# Egypt Crop Risk API

The crop-risk notebook (`crop_project_short.ipynb`) and its Streamlit app, served as
an HTTP API instead of a UI. Same data, same models, same honest numbers — but a
JSON contract that a phone app, a dashboard or a notebook can call.

The Streamlit sliders are now one `POST /v1/risk/report` request:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/risk/report \
  -H "Content-Type: application/json" \
  -d '{"crop": "Wheat", "soil": "Sandy", "water_percent": 70, "stage": "flowering"}'
```

```json
{
  "crop": "Wheat",
  "target_year": 2025,
  "latest_yield_kg_ha": 6777.7,
  "forecast_kg_ha": 6777.7,
  "expected_yield_kg_ha": 5456.0,
  "model_risk_pct": 7.35,
  "water_loss_pct": 19.5,
  "soil_points": 5.0,
  "total_score": 31.85,
  "risk_level": "medium",
  "ky": 0.65,
  "drivers": [
    {"feature": "gap", "share_pct": 73.2, "direction": "lowers", "...": "..."},
    {"feature": "ch2", "share_pct": 19.9, "direction": "lowers", "...": "..."},
    {"feature": "ch1", "share_pct": 6.8, "direction": "lowers", "...": "..."}
  ],
  "assumptions": ["..."]
}
```

## Run it

```bash
py -m pip install -r requirements.txt

fastapi dev api/main.py          # reload on save, http://127.0.0.1:8000/docs
fastapi run api/main.py          # production
```

No Streamlit, no `shap`, no matplotlib: the risk explanation is computed in
closed form (see *Why no shap* below).

## Endpoints

| Method | Path | Answers |
|---|---|---|
| GET | `/v1/health` | Is the service up, and how big is the dataset? |
| GET | `/v1/meta` | Split year, thresholds, soils, stages, Ky values, assumptions |
| GET | `/v1/crops` | The 77 usable crops with their history span |
| GET | `/v1/crops/{item}/history` | Annual yield series for one crop |
| POST | `/v1/risk/report` | The farmer report: risk, expected yield, drivers |
| GET | `/v1/risk/ranking?limit=10` | Crops most likely to drop more than 10 % |
| GET | `/v1/models/metrics` | Out-of-sample scores for naive / linear / forest, and the risk model |
| GET | `/docs` | Swagger UI generated from the same schemas |

Interactive docs: **`/docs`**. Machine-readable: `/openapi.json`.

## What the API promises

- **Year-based split, never random.** Fit on ≤ 2015, judge on 2016–2024, so no
  future information reaches training.
- **Scaling after the split.** `StandardScaler` is fitted on training rows only.
- **The baseline is served as the forecast.** Next year = last year won on the
  held-out years (MAPE 6.4 % vs 10.1 % for random forest and 13.6 % for linear
  regression). `/v1/models/metrics` publishes exactly that instead of hiding it.
- **Explanations are exact, not approximate.** For a logistic regression, SHAP
  values are `coef_j × (x_j − mean_j)` in log-odds, which is what
  `shap.LinearExplainer` computes. Doing it in closed form drops a heavy
  dependency and keeps the report reproducible to the last decimal.

## The risk rules, and which parts are guesses

| Part | Formula | Status |
|---|---|---|
| Model risk | logistic regression on `ch1`, `ch2`, `gap` | fitted, weak signal (AUC ≈ 0.60) |
| Water loss | `Ky × (100 − water_percent)` | FAO Ky for wheat; `Ky = 1.0` for other crops is an assumption |
| Soil points | Sandy 5, Saline 8, Loam/Clay 0 | **assumption, no source** |
| Total | `min(model + water + soil, 100)` | presentation choice |

Every report carries those assumptions in its own `assumptions` array, so a
client can never present a guess as a sourced number.

`Ky` for wheat by stage: vegetative 0.2, flowering 0.65, grain filling 0.55,
ripening 0.1 (FAO; ripening is the loosest of the four).

## Project layout

```
engine.py                 data + models + report logic (importable, testable)
api/main.py               FastAPI app, routers, lifespan (one fit per container)
api/schemas.py            request/response contract
data/…Raw.csv             FAOSTAT export
tests/test_api.py         21 contract tests, including the notebook's worked examples
```

## Tests

```bash
py -m pytest -q      # 21 passed
```

They pin the notebook's numbers: naive MAPE 6.38 %, risk AUC 0.597, base rate
8.58 %, riskiest decile 22.4 %, and Farmer B's total score of 31.85 / 100.

## Deploy

`Dockerfile` runs the same app on any container host:

```bash
docker build -t crop-risk-api .
docker run -p 8000:8000 crop-risk-api
```

## الخلاصة بالعربي

المشروع بقى **خدمة FastAPI** بدل واجهة Streamlit: نفس الداتا ونفس الموديلات
ونفس الأرقام، بس على شكل JSON. أهم نقطتين:

1. **التوقع الأساسي هو Naive** (السنة القادمة = السنة الحالية) لأنه كان الأدق
   فعلاً على 2016–2024، والـ API بيقول ده بصراحة في `/v1/models/metrics`.
2. **أي رقم مبني على افتراض مكتوب صراحة** جوه كل رد في `assumptions` — نقاط
   التربة و`Ky` لغير القمح لسه محتاجين مصدر.
