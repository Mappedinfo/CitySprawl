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
    assert "river_areas" in body
    assert "terrain_class_preview" in body["terrain"]
    assert all("width_m" in e and "render_order" in e for e in body["roads"]["edges"])
    assert "river_coverage_ratio" in body["metrics"]
    assert "river_area_clipped_ratio" in body["metrics"]
    assert "visual_envelope_area_ratio" in body["metrics"]
    assert "road_edge_count_by_class" in body["metrics"]
    assert "generation_profile" in body["metrics"]
    assert body.get("visual_envelope") is not None
    assert any(e.get("path_points") for e in body["roads"]["edges"])
    assert "blocks" in body
    assert "parcels" in body
    assert "pedestrian_paths" in body


def test_generate_staged_success():
    payload = {
        "seed": 8,
        "grid_resolution": 96,
        "terrain": {"noise_octaves": 4, "relief_strength": 1.0},
        "hydrology": {"enable": True, "accum_threshold": 0.02, "min_river_length_m": 80.0},
        "hubs": {"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 80.0},
        "roads": {"k_neighbors": 4, "loop_budget": 2, "branch_steps": 1, "slope_penalty": 2.0, "river_cross_penalty": 200.0},
        "naming": {"provider": "mock"},
    }
    res = client.post("/api/v1/generate_staged", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["final_artifact"]["meta"]["seed"] == 8
    assert [s["stage_id"] for s in body["stages"]] == [
        "terrain",
        "analysis",
        "infrastructure",
        "traffic",
        "final_preview",
    ]
    assert body["stages"][0]["layers"]["terrain_class_preview"] is not None
    assert "river_area_polygons" in body["stages"][0]["layers"]
    assert body["stages"][3]["layers"]["traffic_edge_flows"]
    assert "land_blocks" in body["stages"][4]["layers"]
    assert body["stages"][1]["layers"].get("visual_envelope") is not None
    assert body["final_artifact"]["metrics"]["river_coverage_ratio"] >= 0.0


def test_generate_v2_success():
    payload = {
        "seed": 9,
        "grid_resolution": 96,
        "terrain": {"noise_octaves": 4, "relief_strength": 1.0},
        "hydrology": {"enable": True, "accum_threshold": 0.02, "min_river_length_m": 80.0},
        "hubs": {"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 80.0},
        "roads": {
            "k_neighbors": 4,
            "loop_budget": 2,
            "branch_steps": 1,
            "slope_penalty": 2.0,
            "river_cross_penalty": 200.0,
            "collector_generator": "classic_turtle",
            "classic_probe_step_m": 20.0,
            "classic_seed_spacing_m": 220.0,
            "local_generator": "classic_sprawl",
            "local_classic_probe_step_m": 16.0,
            "local_classic_seed_spacing_m": 90.0,
            "local_classic_continue_prob": 0.6,
            "local_classic_culdesac_prob": 0.9,
        },
        "naming": {"provider": "mock"},
    }
    res = client.post("/api/v2/generate", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert "final_artifact" in body
    assert "stages" in body
    assert body["final_artifact"]["metrics"]["road_edge_count_by_class"] is not None
    assert "degraded_mode" in body["final_artifact"]["metrics"]
    assert "land_blocks" in body["stages"][-1]["layers"]
    assert "parcel_lots" in body["stages"][-1]["layers"]
    assert not any("Tensor" in note for note in body["final_artifact"]["metrics"].get("notes", []))
    metrics = body["final_artifact"]["metrics"]
    assert "collector_classic_arterial_t_attach_count" in metrics
    assert "collector_classic_riverfront_seed_count" in metrics
    assert "local_culdesac_edge_count_final" in metrics
    assert "local_culdesac_preserved_ratio" in metrics


def test_generate_v2_accepts_legacy_tensor_collector_fields_for_compat():
    payload = {
        "seed": 10,
        "grid_resolution": 96,
        "terrain": {"noise_octaves": 4, "relief_strength": 1.0},
        "hydrology": {"enable": True, "accum_threshold": 0.02, "min_river_length_m": 80.0},
        "hubs": {"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 80.0},
        "roads": {
            "collector_generator": "tensor_streamline",
            "tensor_seed_spacing_m": 240.0,
            "tensor_step_m": 22.0,
            "local_generator": "classic_sprawl",
            "local_classic_probe_step_m": 18.0,
        },
        "naming": {"provider": "mock"},
    }
    res = client.post("/api/v2/generate", json=payload)
    assert res.status_code == 200


def test_generate_validation_error():
    res = client.post("/api/v1/generate", json={"seed": -1})
    assert res.status_code == 422


def test_generate_staged_validation_error():
    res = client.post("/api/v1/generate_staged", json={"seed": -1})
    assert res.status_code == 422


def test_generate_v2_validation_error():
    res = client.post("/api/v2/generate", json={"seed": -1})
    assert res.status_code == 422
