"""Crop risk engine — the notebook's logic, in one importable place.

Everything the API serves comes from here, so the numbers in the docs, the
tests and the endpoints cannot drift apart.

The pipeline is the one from ``crop_project_short.ipynb``:

1. keep only ``Element == "Yield"`` rows (the original bug was mixing area,
   production and yield into one ``Value`` column);
2. build per-crop lags (``lag1..lag3``, ``mean3``);
3. split by year — train up to 2015, test from 2016 — never randomly;
4. scale after the split, fitting on training rows only;
5. compare against the naive baseline that repeats last year's value.

The risk model is a logistic regression over three scale-free changes
(``ch1``, ``ch2``, ``gap``), and the farmer report adds the FAO water-loss
rule and the soil points on top of that probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

DATA_FILE = Path(__file__).resolve().parent / "data" / "FAOSTAT_Egypt_Agriculture_Raw.csv"

SPLIT_YEAR = 2015
DECLINE_THRESHOLD = 0.10
MIN_YEARS_FOR_FORECAST = 4

# FAO yield response factor (Ky) for wheat, by growth stage.
# Source: FAO crop information page for wheat; flowering is the most sensitive.
KY_WHEAT = {"vegetative": 0.2, "flowering": 0.65, "grain_filling": 0.55, "ripening": 0.1}
KY_OTHER_CROPS = 1.0  # ASSUMPTION: no stage-specific source for other crops

# ASSUMPTION (no source yet): small penalty per soil type.
SOIL_POINTS = {"Loam": 0, "Clay": 0, "Sandy": 5, "Saline": 8}

RISK_FEATURES = ["ch1", "ch2", "gap"]
FEATURE_LABELS = {
    "ch1": "Last year's change",
    "ch2": "Change the year before",
    "gap": "Last year vs 3-year average",
}

RISK_BANDS = {"low": (0, 15), "medium": (15, 35), "high": (35, 100.0001)}

STAGE_LABELS = {
    "vegetative": "Vegetative",
    "flowering": "Flowering",
    "grain_filling": "Grain filling",
    "ripening": "Ripening",
}

ASSUMPTIONS = [
    "Soil points (Sandy 5, Saline 8, Loam/Clay 0) are an assumption with no source yet.",
    "Ripening Ky = 0.1 and Ky = 1.0 for every crop other than wheat are assumptions.",
    "Risk bands (15 / 35) are a presentation choice, not a validated cut-off.",
    "The drop-probability model is a weak signal: AUC around 0.6 (0.5 is random).",
    "About 32 % of FAOSTAT values are estimated or imputed, not official.",
]


def risk_band(score: float) -> str:
    if score < 15:
        return "low"
    if score < 35:
        return "medium"
    return "high"


def _metrics(y_true: pd.Series, y_pred: np.ndarray) -> dict:
    return {
        "r2": float(r2_score(y_true, y_pred)),
        "mae_kg_ha": float(mean_absolute_error(y_true, y_pred)),
        "mape_pct": float((np.abs(y_true - y_pred) / y_true).mean() * 100),
    }


@dataclass(frozen=True)
class Crop:
    item: str
    years: int
    first_year: int
    last_year: int
    latest_yield: float


class Engine:
    """Loads the CSV once, fits the models once, then answers questions."""

    def __init__(self, data_file: Path | str = DATA_FILE) -> None:
        raw = pd.read_csv(data_file)

        yields = (
            raw.loc[raw["Element"] == "Yield", ["Item", "Year", "Value"]]
            .dropna()
            .query("Value > 0")
            .sort_values(["Item", "Year"])
            .reset_index(drop=True)
        )
        self._yields = yields

        counts = yields.groupby("Item")["Value"].count()
        self._forecastable = set(counts[counts >= MIN_YEARS_FOR_FORECAST].index)

        self._fit_forecast_models()
        self._fit_risk_model()
        self._risk_rows = self._build_risk_rows()
        self.target_year = int(yields["Year"].max()) + 1

    # ------------------------------------------------------------------ data
    @property
    def records(self) -> int:
        return int(len(self._yields))

    def crops(self) -> list[Crop]:
        grouped = self._yields.groupby("Item")
        return [
            Crop(
                item=str(item),
                years=int(len(group)),
                first_year=int(group["Year"].min()),
                last_year=int(group["Year"].max()),
                latest_yield=float(group.sort_values("Year")["Value"].iloc[-1]),
            )
            for item, group in grouped
            if item in self._forecastable
        ]

    def resolve(self, item: str) -> str | None:
        """Case-insensitive crop lookup, so `wheat` and `Wheat` both work."""
        for known in self._forecastable:
            if known.lower() == item.strip().lower():
                return known
        return None

    def history(self, item: str) -> list[dict]:
        rows = self._yields[self._yields["Item"] == item].sort_values("Year")
        return [
            {"year": int(year), "value": float(value)}
            for year, value in zip(rows["Year"], rows["Value"])
        ]

    def _lags(self) -> pd.DataFrame:
        frame = self._yields.copy()
        grouped = frame.groupby("Item")["Value"]
        frame["lag1"] = grouped.shift(1)
        frame["lag2"] = grouped.shift(2)
        frame["lag3"] = grouped.shift(3)
        frame = frame.dropna()
        frame["mean3"] = (frame["lag1"] + frame["lag2"] + frame["lag3"]) / 3
        return frame

    # -------------------------------------------------------------- forecast
    def _fit_forecast_models(self) -> None:
        frame = self._lags()
        modelled = pd.get_dummies(
            frame[["Item", "Year", "lag1", "lag2", "mean3", "Value"]],
            columns=["Item"],
            dtype=int,
            drop_first=True,
        )
        features = modelled.drop(columns=["Value"])
        target = modelled["Value"]

        train = features["Year"] <= SPLIT_YEAR
        x_train, x_test = features[train], features[~train]
        y_train, y_test = target[train], target[~train]

        numeric = ["Year", "lag1", "lag2", "mean3"]
        scaler = StandardScaler().fit(x_train[numeric])  # fit on training rows only
        x_train_s = x_train.copy()
        x_test_s = x_test.copy()
        x_train_s[numeric] = scaler.transform(x_train[numeric])
        x_test_s[numeric] = scaler.transform(x_test[numeric])

        linear = LinearRegression().fit(x_train_s, y_train)
        forest = RandomForestRegressor(random_state=42).fit(x_train_s, y_train)

        self.forecast_metrics = {
            "naive": _metrics(y_test, x_test["lag1"].to_numpy()),
            "linear": _metrics(y_test, linear.predict(x_test_s)),
            "forest": _metrics(y_test, forest.predict(x_test_s)),
        }
        self.test_years = f"{int(x_test['Year'].min())}-{int(x_test['Year'].max())}"
        self.test_rows = int(len(x_test))

    # ------------------------------------------------------------------ risk
    def _build_risk_rows(self) -> pd.DataFrame:
        frame = self._lags()
        frame["ch1"] = frame["lag1"] / frame["lag2"] - 1
        frame["ch2"] = frame["lag2"] / frame["lag3"] - 1
        frame["gap"] = frame["lag1"] / frame["mean3"] - 1
        frame["drop"] = ((frame["Value"] / frame["lag1"] - 1) < -DECLINE_THRESHOLD).astype(int)
        return frame.replace([np.inf, -np.inf], np.nan).dropna(subset=RISK_FEATURES)

    def _fit_risk_model(self) -> None:
        frame = self._build_risk_rows()
        train = frame["Year"] <= SPLIT_YEAR
        x_train = frame.loc[train, RISK_FEATURES]
        y_train = frame.loc[train, "drop"]
        x_test = frame.loc[~train, RISK_FEATURES]
        y_test = frame.loc[~train, "drop"]

        scaler = StandardScaler().fit(x_train)
        model = LogisticRegression().fit(scaler.transform(x_train), y_train)

        probability = model.predict_proba(scaler.transform(x_test))[:, 1]
        cutoff = max(1, int(len(probability) * 0.10))
        top = np.argsort(-probability)[:cutoff]
        base_rate = float(y_test.mean())

        self.risk_model = model
        self.risk_scaler = scaler
        self.risk_metrics = {
            "auc": float(roc_auc_score(y_test, probability)),
            "base_rate": base_rate,
            "top_decile_rate": float(y_test.to_numpy()[top].mean()),
            "lift": float(y_test.to_numpy()[top].mean() / base_rate),
        }

    def _probability(self, row: pd.DataFrame) -> float:
        scaled = self.risk_scaler.transform(row[RISK_FEATURES])
        return float(self.risk_model.predict_proba(scaled)[0, 1])

    def _shap(self, row: pd.DataFrame) -> list[dict]:
        """Exact SHAP for a linear model: coef_j * (x_j - mean_j), in log-odds.

        This is what ``shap.LinearExplainer`` computes for a logistic
        regression; doing it in closed form removes a heavy dependency.
        """
        scaled = self.risk_scaler.transform(row[RISK_FEATURES])[0]
        coef = self.risk_model.coef_[0]
        contrib = coef * scaled  # scaled features are already centred on 0

        total = float(np.abs(contrib).sum()) or 1.0
        drivers = [
            {
                "feature": feature,
                "label": FEATURE_LABELS[feature],
                "value": float(row[feature].iloc[0]),
                "contribution": float(value),
                "share_pct": float(abs(value) / total * 100),
                "direction": "raises" if value > 0 else "lowers",
            }
            for feature, value in zip(RISK_FEATURES, contrib)
        ]
        return sorted(drivers, key=lambda row_: row_["share_pct"], reverse=True)

    def ranking(self, limit: int = 10) -> list[dict]:
        """Current drop probability for every crop, worst first."""
        rows = []
        for crop in self.crops():
            probability = self._probability(self._latest_row(crop.item))
            rows.append(
                {
                    "item": crop.item,
                    "probability": probability,
                    "band": risk_band(probability * 100),
                    "latest_yield": crop.latest_yield,
                    "last_change": float(self._latest_row(crop.item)["ch1"].iloc[0]),
                }
            )
        rows.sort(key=lambda row: row["probability"], reverse=True)
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows[:limit]

    # ---------------------------------------------------------------- report
    def _latest_row(self, item: str) -> pd.DataFrame:
        rows = self._risk_rows[self._risk_rows["Item"] == item].sort_values("Year")
        return rows.tail(1)

    def report(self, item: str, soil: str, water_percent: float, stage: str) -> dict:
        crop = self.resolve(item)
        if crop is None:
            raise KeyError(item)

        row = self._latest_row(crop)
        latest_yield = float(row["Value"].iloc[0])

        model_risk = self._probability(row) * 100
        ky = KY_WHEAT[stage] if crop == "Wheat" else KY_OTHER_CROPS
        water_loss = min(ky * (100 - water_percent), 100.0)
        soil_points = float(SOIL_POINTS[soil])
        total = min(model_risk + water_loss + soil_points, 100.0)

        return {
            "crop": crop,
            "last_observed_year": int(row["Year"].iloc[0]),
            "target_year": int(row["Year"].iloc[0]) + 1,
            "latest_yield_kg_ha": latest_yield,
            "forecast_kg_ha": latest_yield,  # naive: next year = last year
            "expected_yield_kg_ha": latest_yield * (1 - water_loss / 100),
            "model_risk_pct": model_risk,
            "water_loss_pct": water_loss,
            "soil_points": soil_points,
            "total_score": total,
            "risk_level": risk_band(total),
            "ky": ky,
            "inputs": {"soil": soil, "water_percent": water_percent, "stage": stage},
            "drivers": self._shap(row),
            "assumptions": ASSUMPTIONS,
        }
