import json
import time
from pathlib import Path

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
        "start",
        "terrain",
        "rivers",
        "hubs",
        "roads",
        "artifact",
        "analysis",
        "traffic",
        "buildings",
        "parcels",
        "stages",
        "done",
    ]
    stages_by_id = {s["stage_id"]: s for s in body["stages"]}
    assert stages_by_id["terrain"]["layers"]["terrain_class_preview"] is not None
    assert "river_area_polygons" in stages_by_id["terrain"]["layers"]
    assert stages_by_id["traffic"]["layers"]["traffic_edge_flows"]
    assert "land_blocks" in stages_by_id["done"]["layers"]
    assert stages_by_id["analysis"]["layers"].get("visual_envelope") is not None
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
            "local_geometry_mode": "classic_sprawl_rerouted",
            "local_reroute_coverage": "selective",
            "local_reroute_min_length_m": 60.0,
            "local_reroute_max_edges_per_city": 80,
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
    assert "major_local_classic_arterial_t_attach_count" in metrics
    assert "major_local_classic_riverfront_seed_count" in metrics
    assert "local_culdesac_edge_count_final" in metrics
    assert "local_culdesac_preserved_ratio" in metrics
    assert "local_reroute_candidate_count" in metrics
    assert "local_reroute_applied_count" in metrics
    assert "local_two_point_edge_ratio" in metrics


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
            "local_geometry_mode": "classic_sprawl_rerouted",
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


def test_generate_v2_async_progress_and_result():
    payload = {
        "seed": 13,
        "grid_resolution": 96,
        "terrain": {"noise_octaves": 4, "relief_strength": 1.0},
        "hydrology": {"enable": True, "accum_threshold": 0.02, "min_river_length_m": 80.0},
        "hubs": {"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 80.0},
        "roads": {
            "collector_generator": "classic_turtle",
            "local_generator": "classic_sprawl",
            "local_geometry_mode": "classic_sprawl_rerouted",
            "local_reroute_coverage": "selective",
            "local_reroute_max_edges_per_city": 40,
        },
        "naming": {"provider": "mock"},
    }
    start_res = client.post("/api/v2/generate_async", json=payload)
    assert start_res.status_code == 200
    body = start_res.json()
    job_id = body["job_id"]
    assert job_id

    last_seq = 0
    final_status = None
    for _ in range(800):
        res = client.get(f"/api/v2/jobs/{job_id}", params={"since_seq": last_seq})
        assert res.status_code == 200
        status = res.json()
        final_status = status
        assert "progress" in status
        assert "logs" in status
        if status.get("logs"):
            last_seq = max(last_seq, max(int(l.get("seq", 0)) for l in status["logs"]))
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert final_status is not None
    assert final_status["status"] == "completed", final_status
    assert final_status["result_ready"] is True
    assert final_status["last_log_seq"] >= 1

    run_logs_res = client.get(f"/api/v2/runs/{job_id}/logs")
    assert run_logs_res.status_code == 200
    run_logs = run_logs_res.json()
    assert run_logs["run_id"] == job_id
    assert run_logs["job_id"] == job_id
    assert run_logs["last_seq"] >= 1
    assert isinstance(run_logs["logs"], list)
    assert any(log.get("kind") in ("lifecycle", "progress", "phase_timing") for log in run_logs["logs"])

    result_res = client.get(f"/api/v2/jobs/{job_id}/result")
    assert result_res.status_code == 200
    result_body = result_res.json()
    assert "final_artifact" in result_body
    assert "stages" in result_body


def test_generate_stream_complete_event_keeps_final_artifact_object():
    payload = {
        "seed": 21,
        "grid_resolution": 64,
        "terrain": {"noise_octaves": 4, "relief_strength": 1.0},
        "hydrology": {"enable": True, "accum_threshold": 0.02, "min_river_length_m": 80.0},
        "hubs": {"t1_count": 1, "t2_count": 1, "t3_count": 4, "min_distance_m": 80.0},
        "roads": {"k_neighbors": 4, "loop_budget": 1, "branch_steps": 1, "slope_penalty": 2.0, "river_cross_penalty": 200.0},
        "naming": {"provider": "mock"},
    }
    res = client.post("/api/v2/generate_stream", json=payload, headers={"accept": "text/event-stream"})
    assert res.status_code == 200
    text = res.text
    assert "event: complete" in text

    lines = [line.strip() for line in text.splitlines()]
    complete_data = None
    heartbeat_data = None
    progress_phases = []
    saw_heartbeat = False
    saw_complete = False
    saw_batch = False
    for line in lines:
        if line == "event: heartbeat":
            saw_heartbeat = True
            saw_complete = False
            saw_batch = False
            continue
        if line == "event: batch":
            saw_batch = True
            saw_complete = False
            saw_heartbeat = False
            continue
        if line == "event: complete":
            saw_complete = True
            saw_heartbeat = False
            saw_batch = False
            continue
        if saw_heartbeat and line.startswith("data:") and heartbeat_data is None:
            heartbeat_data = json.loads(line[len("data:"):].strip())
            continue
        if saw_batch and line.startswith("data:"):
            batch_data = json.loads(line[len("data:"):].strip())
            for evt in batch_data.get("events", []):
                if str(evt.get("event_type", "")) != "progress":
                    continue
                data = evt.get("data", {}) if isinstance(evt, dict) else {}
                phase = str(data.get("phase", "")) if isinstance(data, dict) else ""
                if phase:
                    progress_phases.append(phase)
            continue
        if saw_complete and line.startswith("data:"):
            complete_data = json.loads(line[len("data:"):].strip())
            break
    assert heartbeat_data is not None
    assert isinstance(heartbeat_data.get("run_id"), str) and heartbeat_data["run_id"]
    assert complete_data is not None
    assert isinstance(complete_data.get("final_artifact"), dict)
    assert "stages" in complete_data
    assert complete_data.get("stream_complete") is True
    assert complete_data.get("run_id") == heartbeat_data.get("run_id")
    road_progress_phases = [p for p in progress_phases if p.startswith("roads")]
    assert road_progress_phases
    assert any(p != "roads" for p in road_progress_phases)
    assert any(
        p.startswith("roads_collector.") or p.startswith("roads_local.")
        for p in road_progress_phases
    ), road_progress_phases

    run_logs_res = client.get(f"/api/v2/runs/{complete_data['run_id']}/logs", params={"limit": 2000})
    assert run_logs_res.status_code == 200
    run_logs = run_logs_res.json()
    assert run_logs["run_id"] == complete_data["run_id"]
    assert run_logs["last_seq"] >= 1
    assert any(log.get("event") == "sse_stream_started" for log in run_logs["logs"])
    assert any(log.get("kind") in ("lifecycle", "progress", "phase_timing") for log in run_logs["logs"])


def test_load_staged_json_roundtrip(tmp_path: Path):
    payload = {
        "seed": 22,
        "grid_resolution": 64,
        "terrain": {"noise_octaves": 4, "relief_strength": 1.0},
        "hydrology": {"enable": True, "accum_threshold": 0.02, "min_river_length_m": 80.0},
        "hubs": {"t1_count": 1, "t2_count": 1, "t3_count": 4, "min_distance_m": 80.0},
        "roads": {"k_neighbors": 4, "loop_budget": 1, "branch_steps": 1, "slope_penalty": 2.0, "river_cross_penalty": 200.0},
        "naming": {"provider": "mock"},
    }
    generated = client.post("/api/v2/generate", json=payload)
    assert generated.status_code == 200
    data = generated.json()

    json_path = tmp_path / "staged_city.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")

    loaded = client.post("/api/v2/load_staged_json", json={"path": str(json_path)})
    assert loaded.status_code == 200
    loaded_body = loaded.json()
    assert loaded_body["final_artifact"]["meta"]["seed"] == 22
    assert [s["stage_id"] for s in loaded_body["stages"]] == [s["stage_id"] for s in data["stages"]]
