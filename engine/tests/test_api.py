from fastapi.testclient import TestClient

from engine.api.app import app

client = TestClient(app)


def test_health():
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_schema():
    res = client.get("/api/v1/schema")
    assert res.status_code == 200
    body = res.json()
    assert "properties" in body
    assert "seed" in body["properties"]


def test_generate_success():
    payload = {
        "seed": 7,
        "grid_resolution": 96,
        "terrain": {"noise_octaves": 4, "relief_strength": 1.0},
        "hydrology": {"enable": True, "accum_threshold": 0.02, "min_river_length_m": 80.0},
        "hubs": {"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 80.0},
        "roads": {"k_neighbors": 4, "loop_budget": 2, "branch_steps": 1, "slope_penalty": 2.0, "river_cross_penalty": 200.0},
        "naming": {"provider": "mock"},
    }
    res = client.post("/api/v1/generate", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["meta"]["seed"] == 7
    assert len(body["hubs"]) == 11
    assert "metrics" in body


def test_generate_validation_error():
    res = client.post("/api/v1/generate", json={"seed": -1})
    assert res.status_code == 422
