"""Contract tests — the numbers the notebook reported must survive the port."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------- meta
def test_health(client):
    body = client.get("/v1/health").json()
    assert body["status"] == "ok"
    assert body["crops"] > 70
    assert body["years"] == "1990-2024"


def test_meta_publishes_the_rules(client):
    body = client.get("/v1/meta").json()
    assert body["split_year"] == 2015
    assert body["target_year"] == 2025
    assert body["decline_threshold"] == 0.10
    assert set(body["soils"]) == {"Loam", "Clay", "Sandy", "Saline"}
    assert set(body["stages"]) == {"vegetative", "flowering", "grain_filling", "ripening"}
    # The rule-based half must never look sourced when it is not.
    assert any("assumption" in line.lower() for line in body["assumptions"])


def test_root_serves_the_interface(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "How risky is this season" in response.text
    assert response.headers["cache-control"].startswith("no-store")


def test_interface_assets_are_served(client):
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200
    assert client.get("/assets/field.webp").status_code == 200


# --------------------------------------------------------------------- crops
def test_crops_are_listed_with_their_history_span(client):
    body = client.get("/v1/crops").json()
    assert body["count"] == len(body["items"])
    wheat = next(row for row in body["items"] if row["item"] == "Wheat")
    assert wheat["first_year"] == 1990
    assert wheat["last_year"] == 2024
    assert wheat["years"] >= 30


def test_history_is_case_insensitive_and_sorted(client):
    body = client.get("/v1/crops/wheat/history").json()
    years = [point["year"] for point in body["points"]]
    assert body["item"] == "Wheat"
    assert years == sorted(years)
    assert years[-1] == 2024


def test_unknown_crop_is_a_404_with_a_useful_message(client):
    response = client.get("/v1/crops/nope/history")
    assert response.status_code == 404
    assert "GET /v1/crops" in response.json()["detail"]


# -------------------------------------------------------------------- report
def test_farmer_b_matches_the_notebook(client):
    """Sandy soil, 70 % water, flowering wheat: the notebook's worked example."""
    response = client.post(
        "/v1/risk/report",
        json={"crop": "Wheat", "soil": "Sandy", "water_percent": 70, "stage": "flowering"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["target_year"] == 2025
    assert body["forecast_kg_ha"] == pytest.approx(6777.7, abs=0.5)  # naive = last observed
    assert body["water_loss_pct"] == pytest.approx(19.5, abs=0.01)  # Ky 0.65 x 30 %
    assert body["expected_yield_kg_ha"] == pytest.approx(5456.0, abs=2)
    assert body["soil_points"] == 5
    assert body["total_score"] == pytest.approx(
        body["model_risk_pct"] + body["water_loss_pct"] + body["soil_points"], abs=1e-6
    )
    assert body["risk_level"] == "medium"


def test_farmer_a_has_no_water_loss(client):
    """Loam, full water, vegetative stage: only the model contributes."""
    body = client.post(
        "/v1/risk/report",
        json={"crop": "Wheat", "soil": "Loam", "water_percent": 100, "stage": "vegetative"},
    ).json()
    assert body["water_loss_pct"] == 0
    assert body["soil_points"] == 0
    assert body["total_score"] == pytest.approx(body["model_risk_pct"], abs=1e-6)
    assert body["risk_level"] == "low"


def test_water_loss_is_capped_at_100(client):
    body = client.post(
        "/v1/risk/report",
        json={"crop": "Wheat", "soil": "Saline", "water_percent": 0, "stage": "flowering"},
    ).json()
    assert body["water_loss_pct"] == 65  # Ky 0.65 x 100 % deficit
    assert body["expected_yield_kg_ha"] == pytest.approx(body["forecast_kg_ha"] * 0.35, rel=1e-6)
    assert body["total_score"] <= 100


def test_other_crops_use_the_flat_ky_and_say_so(client):
    body = client.post(
        "/v1/risk/report",
        json={"crop": "Rice", "soil": "Clay", "water_percent": 80, "stage": "flowering"},
    ).json()
    assert body["ky"] == 1.0
    assert body["water_loss_pct"] == pytest.approx(20.0, abs=0.01)


def test_drivers_explain_the_model_and_sum_to_a_hundred(client):
    body = client.post("/v1/risk/report", json={"crop": "Wheat"}).json()
    drivers = body["drivers"]
    assert {driver["feature"] for driver in drivers} == {"ch1", "ch2", "gap"}
    assert sum(driver["share_pct"] for driver in drivers) == pytest.approx(100, abs=0.5)
    assert all(driver["direction"] in {"raises", "lowers"} for driver in drivers)
    assert len(body["assumptions"]) >= 3


@pytest.mark.parametrize(
    "payload",
    [
        {"crop": "Wheat", "soil": "Mars"},
        {"crop": "Wheat", "stage": "harvest"},
        {"crop": "Wheat", "water_percent": 120},
        {"crop": "Wheat", "water_percent": -1},
        {},
    ],
)
def test_invalid_input_is_rejected(client, payload):
    assert client.post("/v1/risk/report", json=payload).status_code == 422


def test_unknown_crop_in_report_is_404(client):
    response = client.post("/v1/risk/report", json={"crop": "Nonexistent"})
    assert response.status_code == 404


# ------------------------------------------------------------------- ranking
def test_ranking_is_sorted_and_respects_the_limit(client):
    body = client.get("/v1/risk/ranking?limit=5").json()
    assert body["count"] == 5
    probabilities = [row["probability"] for row in body["rows"]]
    assert probabilities == sorted(probabilities, reverse=True)
    assert [row["rank"] for row in body["rows"]] == [1, 2, 3, 4, 5]
    assert body["threshold"] == 0.10


def test_ranking_limit_is_validated(client):
    assert client.get("/v1/risk/ranking?limit=0").status_code == 422
    assert client.get("/v1/risk/ranking?limit=500").status_code == 422


# ------------------------------------------------------------------- metrics
def test_metrics_confirm_the_naive_baseline_wins(client):
    body = client.get("/v1/models/metrics").json()
    scores = {row["model"]: row for row in body["models"]}

    assert body["split_year"] == 2015
    assert body["test_years"] == "2016-2024"
    assert scores["naive"]["mape_pct"] < scores["forest"]["mape_pct"] < scores["linear"]["mape_pct"]
    assert scores["naive"]["mape_pct"] == pytest.approx(6.38, abs=0.05)

    risk = body["risk"]
    assert 0.5 <= risk["auc"] <= 0.75          # weak but better than a coin flip
    assert risk["top_decile_rate"] > risk["base_rate"]  # the ranking concentrates risk


def test_responses_are_never_cached(client):
    assert client.get("/v1/health").headers["cache-control"] == "no-store"
