from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI
from fastapi import HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from engine.generator import generate_city, generate_city_staged
from engine.models import CityArtifact, GenerateConfig, StagedCityResponse
from engine.pydantic_compat import model_json_schema, model_dump

_LOGGER = logging.getLogger("citygen.api.jobs")
_JOBS_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}

# Type for streaming callback
StreamCallback = Callable[[Dict[str, Any]], None]


class LoadStagedJsonRequest(BaseModel):
    path: str = Field(min_length=1)


class StreamBuffer:
    """Buffer for batching stream events to reduce network overhead."""

    def __init__(self, flush_interval_ms: float = 80.0, max_batch_size: int = 15):
        self.buffer: List[Dict[str, Any]] = []
        self.last_flush = time.time()
        self.flush_interval_ms = flush_interval_ms
        self.max_batch_size = max_batch_size
        self.sequence = 0
        self._lock = threading.Lock()
        self._urgent_flush = False

    def add(self, event: Dict[str, Any], *, urgent: bool = False) -> None:
        with self._lock:
            self.sequence += 1
            event["sequence"] = self.sequence
            event["timestamp_ms"] = int(time.time() * 1000)
            self.buffer.append(event)
            self._urgent_flush = self._urgent_flush or bool(urgent)

    def should_flush(self) -> bool:
        with self._lock:
            if not self.buffer:
                return False
            return (
                self._urgent_flush
                or
                len(self.buffer) >= self.max_batch_size
                or (time.time() - self.last_flush) * 1000 >= self.flush_interval_ms
            )

    def flush(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.buffer:
                return None
            events = self.buffer
            self.buffer = []
            self.last_flush = time.time()
            self._urgent_flush = False
            return {"event": "batch", "data": json.dumps({"events": events})}

    def get_pending_count(self) -> int:
        with self._lock:
            return len(self.buffer)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_job_log(job: Dict[str, Any], *, phase: str, progress: float, message: str) -> None:
    seq = int(job.get("last_log_seq", 0)) + 1
    entry = {
        "seq": seq,
        "ts": _now_iso(),
        "phase": str(phase),
        "progress": float(max(0.0, min(1.0, progress))),
        "message": str(message),
    }
    logs = job.setdefault("logs", [])
    logs.append(entry)
    if len(logs) > 400:
        del logs[: len(logs) - 400]
    job["last_log_seq"] = seq
    job["updated_at"] = entry["ts"]
    job["phase"] = entry["phase"]
    job["progress"] = entry["progress"]
    job["message"] = entry["message"]
    _LOGGER.info("[job:%s] %s %.0f%% %s", job.get("id"), entry["phase"], entry["progress"] * 100.0, entry["message"])


def _job_status_payload(job: Dict[str, Any], *, since_seq: int = 0) -> Dict[str, Any]:
    logs = [l for l in list(job.get("logs", [])) if int(l.get("seq", 0)) > int(since_seq)]
    return {
        "job_id": job["id"],
        "status": job.get("status", "queued"),
        "progress": float(job.get("progress", 0.0)),
        "phase": str(job.get("phase", "")),
        "message": str(job.get("message", "")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
        "logs": logs,
        "last_log_seq": int(job.get("last_log_seq", 0)),
        "result_ready": bool(job.get("result") is not None and str(job.get("status")) == "completed"),
    }


def _run_generate_job(job_id: str, payload: GenerateConfig) -> None:
    def _progress_cb(phase: str, progress: float, message: str) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if not job:
                return
            _append_job_log(job, phase=phase, progress=progress, message=message)

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        _append_job_log(job, phase="queued", progress=0.0, message="Job accepted by backend")
    try:
        result = generate_city_staged(payload, progress_cb=_progress_cb)
    except Exception as exc:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = f"{exc.__class__.__name__}: {exc}"
            _append_job_log(job, phase="failed", progress=float(job.get("progress", 0.0)), message=job["error"])
        return
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["result"] = result
        job["status"] = "completed"
        _append_job_log(job, phase="completed", progress=1.0, message="Result ready")


def _create_generate_job(payload: GenerateConfig) -> str:
    job_id = f"gen-{uuid4().hex[:12]}"
    job = {
        "id": job_id,
        "status": "queued",
        "progress": 0.0,
        "phase": "queued",
        "message": "Queued",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "error": None,
        "logs": [],
        "last_log_seq": 0,
        "result": None,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    thread = threading.Thread(target=_run_generate_job, args=(job_id, payload), daemon=True, name=f"citygen-{job_id}")
    thread.start()
    return job_id


def _validate_staged_response_payload(payload: Any) -> StagedCityResponse:
    if hasattr(StagedCityResponse, "model_validate"):
        return StagedCityResponse.model_validate(payload)  # type: ignore[attr-defined]
    return StagedCityResponse.parse_obj(payload)  # type: ignore[attr-defined]


def _load_staged_json(path_str: str) -> StagedCityResponse:
    path = Path(path_str).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail="json_file_not_found")
    if not path.is_file():
        raise HTTPException(status_code=400, detail="json_path_must_be_file")
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid_json:{exc.msg}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"io_error:{exc}") from exc
    try:
        return _validate_staged_response_payload(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid_staged_city_json:{exc}") from exc


def _default_preset() -> GenerateConfig:
    return GenerateConfig(
        quality={"profile": "balanced", "time_budget_ms": 15000},
        hydrology={
            "enable": True,
            "accum_threshold": 0.015,
            "min_river_length_m": 1000.0,
            "primary_branch_count_max": 4,
            "centerline_smooth_iters": 2,
            "width_taper_strength": 0.35,
            "bank_irregularity": 0.08,
        },
        roads={
            "k_neighbors": 4,
            "loop_budget": 3,
            "branch_steps": 2,
            "slope_penalty": 2.0,
            "river_cross_penalty": 300.0,
            "style": "mixed_organic",
            "collector_spacing_m": 420.0,
            "local_spacing_m": 130.0,
            "collector_jitter": 0.16,
            "local_jitter": 0.22,
            "local_generator": "classic_sprawl",
            "local_classic_probe_step_m": 18.0,
            "local_classic_seed_spacing_m": 110.0,
            "local_classic_max_trace_len_m": 420.0,
            "local_classic_min_trace_len_m": 48.0,
            "local_classic_turn_limit_deg": 54.0,
            "local_classic_branch_prob": 0.62,
            "local_classic_continue_prob": 0.70,
            "local_classic_culdesac_prob": 0.42,
            "local_classic_max_segments_per_block": 28,
            "local_community_seed_count_per_block": 3,
            "local_community_spine_prob": 0.28,
            "local_arterial_setback_weight": 0.5,
            "local_collector_follow_weight": 0.9,
            "river_setback_m": 18.0,
            "minor_bridge_budget": 4,
            "max_local_block_area_m2": 180000.0,
            "collector_generator": "classic_turtle",
            "classic_probe_step_m": 24.0,
            "classic_seed_spacing_m": 260.0,
            "classic_max_trace_len_m": 1800.0,
            "classic_min_trace_len_m": 120.0,
            "classic_turn_limit_deg": 38.0,
            "classic_branch_prob": 0.35,
            "classic_continue_prob": 0.80,
            "classic_culdesac_prob": 0.18,
            "classic_max_queue_size": 2000,
            "classic_max_segments": 1200,
            "slope_straight_threshold_deg": 5.0,
            "slope_serpentine_threshold_deg": 15.0,
            "slope_hard_limit_deg": 22.0,
            "contour_follow_weight": 0.9,
            "arterial_align_weight": 0.6,
            "hub_seek_weight": 0.25,
            "river_snap_dist_m": 28.0,
            "river_parallel_bias_weight": 1.0,
            "river_avoid_weight": 1.2,
        },
    )


def _river_valley_preset() -> GenerateConfig:
    return GenerateConfig(
        seed=17,
        extent_m=10000.0,
        quality={"profile": "hq", "time_budget_ms": 60000},
        terrain={"noise_octaves": 6, "relief_strength": 1.15},
        hydrology={
            "enable": True,
            "accum_threshold": 0.012,
            "min_river_length_m": 1200.0,
            "primary_branch_count_max": 5,
            "centerline_smooth_iters": 2,
            "width_taper_strength": 0.35,
            "bank_irregularity": 0.08,
        },
        hubs={"t1_count": 1, "t2_count": 5, "t3_count": 18, "min_distance_m": 550.0},
        roads={
            "k_neighbors": 4,
            "loop_budget": 4,
            "branch_steps": 2,
            "slope_penalty": 1.8,
            "river_cross_penalty": 260.0,
            "style": "mixed_organic",
            "collector_spacing_m": 320.0,
            "local_spacing_m": 95.0,
            "collector_jitter": 0.16,
            "local_jitter": 0.22,
            "local_generator": "classic_sprawl",
            "local_classic_probe_step_m": 16.0,
            "local_classic_seed_spacing_m": 90.0,
            "local_classic_max_trace_len_m": 460.0,
            "local_classic_min_trace_len_m": 42.0,
            "local_classic_turn_limit_deg": 58.0,
            "local_classic_branch_prob": 0.68,
            "local_classic_continue_prob": 0.74,
            "local_classic_culdesac_prob": 0.38,
            "local_classic_max_segments_per_block": 42,
            "local_community_seed_count_per_block": 4,
            "local_community_spine_prob": 0.32,
            "local_arterial_setback_weight": 0.55,
            "local_collector_follow_weight": 0.95,
            "river_setback_m": 18.0,
            "minor_bridge_budget": 8,
            "max_local_block_area_m2": 180000.0,
            "collector_generator": "classic_turtle",
            "classic_probe_step_m": 22.0,
            "classic_seed_spacing_m": 220.0,
            "classic_max_trace_len_m": 2400.0,
            "classic_min_trace_len_m": 120.0,
            "classic_turn_limit_deg": 35.0,
            "classic_branch_prob": 0.38,
            "classic_continue_prob": 0.82,
            "classic_culdesac_prob": 0.15,
            "classic_max_queue_size": 2800,
            "classic_max_segments": 1800,
            "slope_straight_threshold_deg": 5.0,
            "slope_serpentine_threshold_deg": 14.0,
            "slope_hard_limit_deg": 21.0,
            "contour_follow_weight": 0.95,
            "arterial_align_weight": 0.65,
            "hub_seek_weight": 0.28,
            "river_snap_dist_m": 30.0,
            "river_parallel_bias_weight": 1.1,
            "river_avoid_weight": 1.25,
        },
    )


def _hills_preset() -> GenerateConfig:
    return GenerateConfig(
        seed=103,
        extent_m=10000.0,
        quality={"profile": "preview", "time_budget_ms": 5000},
        terrain={"noise_octaves": 5, "relief_strength": 1.3},
        hydrology={
            "enable": True,
            "accum_threshold": 0.02,
            "min_river_length_m": 1000.0,
            "primary_branch_count_max": 3,
            "centerline_smooth_iters": 1,
            "width_taper_strength": 0.25,
            "bank_irregularity": 0.05,
        },
        hubs={"t1_count": 1, "t2_count": 3, "t3_count": 14, "min_distance_m": 700.0},
        roads={
            "k_neighbors": 3,
            "loop_budget": 2,
            "branch_steps": 1,
            "slope_penalty": 3.0,
            "river_cross_penalty": 420.0,
            "style": "mixed_organic",
            "collector_spacing_m": 520.0,
            "local_spacing_m": 170.0,
            "collector_jitter": 0.12,
            "local_jitter": 0.18,
            "local_generator": "classic_sprawl",
            "local_classic_probe_step_m": 20.0,
            "local_classic_seed_spacing_m": 150.0,
            "local_classic_max_trace_len_m": 320.0,
            "local_classic_min_trace_len_m": 40.0,
            "local_classic_turn_limit_deg": 62.0,
            "local_classic_branch_prob": 0.56,
            "local_classic_continue_prob": 0.66,
            "local_classic_culdesac_prob": 0.48,
            "local_classic_max_segments_per_block": 20,
            "local_community_seed_count_per_block": 2,
            "local_community_spine_prob": 0.22,
            "local_arterial_setback_weight": 0.45,
            "local_collector_follow_weight": 0.8,
            "river_setback_m": 22.0,
            "minor_bridge_budget": 2,
            "max_local_block_area_m2": 220000.0,
            "collector_generator": "classic_turtle",
            "classic_probe_step_m": 26.0,
            "classic_seed_spacing_m": 340.0,
            "classic_max_trace_len_m": 1200.0,
            "classic_min_trace_len_m": 100.0,
            "classic_turn_limit_deg": 42.0,
            "classic_branch_prob": 0.28,
            "classic_continue_prob": 0.78,
            "classic_culdesac_prob": 0.22,
            "classic_max_queue_size": 1200,
            "classic_max_segments": 800,
            "slope_straight_threshold_deg": 5.0,
            "slope_serpentine_threshold_deg": 13.0,
            "slope_hard_limit_deg": 20.0,
            "contour_follow_weight": 1.0,
            "arterial_align_weight": 0.5,
            "hub_seek_weight": 0.2,
            "river_snap_dist_m": 26.0,
            "river_parallel_bias_weight": 0.9,
            "river_avoid_weight": 1.3,
        },
    )


def _presets() -> Dict[str, GenerateConfig]:
    return {
        "default": _default_preset(),
        "river_valley": _river_valley_preset(),
        "hilly_sparse": _hills_preset(),
    }


def create_app() -> FastAPI:
    app = FastAPI(title="GeoAI Urban Sandbox API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/presets")
    def presets() -> Dict[str, Any]:
        return {name: cfg.model_dump() if hasattr(cfg, "model_dump") else cfg.dict() for name, cfg in _presets().items()}

    @app.get("/api/v1/schema")
    def schema() -> Dict[str, Any]:
        return model_json_schema(GenerateConfig)

    @app.post("/api/v1/generate", response_model=CityArtifact)
    def generate(payload: GenerateConfig) -> CityArtifact:
        return generate_city(payload)

    @app.post("/api/v1/generate_staged", response_model=StagedCityResponse)
    def generate_staged(payload: GenerateConfig) -> StagedCityResponse:
        return generate_city_staged(payload)

    @app.post("/api/v2/generate", response_model=StagedCityResponse)
    def generate_v2(payload: GenerateConfig) -> StagedCityResponse:
        return generate_city_staged(payload)

    @app.post("/api/v2/generate_async")
    def generate_v2_async(payload: GenerateConfig) -> Dict[str, Any]:
        job_id = _create_generate_job(payload)
        return {"job_id": job_id, "status": "queued"}

    @app.post("/api/v2/load_staged_json", response_model=StagedCityResponse)
    def load_staged_json(payload: LoadStagedJsonRequest) -> StagedCityResponse:
        return _load_staged_json(payload.path)

    @app.get("/api/v2/jobs/{job_id}")
    def get_generate_job(job_id: str, since_seq: int = Query(default=0, ge=0)) -> Dict[str, Any]:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job_not_found")
            return _job_status_payload(job, since_seq=int(since_seq))

    @app.get("/api/v2/jobs/{job_id}/result", response_model=StagedCityResponse)
    def get_generate_job_result(job_id: str) -> StagedCityResponse:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job_not_found")
            status = str(job.get("status", "queued"))
            if status == "failed":
                raise HTTPException(status_code=409, detail=str(job.get("error") or "job_failed"))
            result = job.get("result")
        if result is None:
            raise HTTPException(status_code=409, detail="result_not_ready")
        return result

    @app.post("/api/v2/generate_stream")
    async def generate_stream(payload: GenerateConfig) -> EventSourceResponse:
        """Stream city generation progress with incremental geometry data via SSE."""

        async def event_generator():
            buffer = StreamBuffer(flush_interval_ms=30.0, max_batch_size=8)
            result_holder: Dict[str, Any] = {"result": None, "error": None, "done": False}
            heartbeat_counter = 0

            _LOGGER.info("SSE stream started")

            def stream_cb(event: Dict[str, Any]) -> None:
                _LOGGER.debug("stream_cb received event: %s", event.get("event_type"))
                buffer.add(event)

            def progress_cb(phase: str, progress: float, message: str) -> None:
                _LOGGER.debug("progress_cb: %s %.0f%% %s", phase, progress * 100, message)
                buffer.add({
                    "event_type": "progress",
                    "data": {"phase": phase, "progress": progress, "message": message},
                }, urgent=True)

            def run_generation() -> None:
                _LOGGER.info("Generation thread started")
                try:
                    result = generate_city_staged(
                        payload,
                        progress_cb=progress_cb,
                        stream_cb=stream_cb,
                    )
                    result_holder["result"] = result
                    _LOGGER.info("Generation completed successfully")
                except Exception as exc:
                    _LOGGER.error("Generation failed: %s", exc)
                    result_holder["error"] = f"{exc.__class__.__name__}: {exc}"
                finally:
                    result_holder["done"] = True

            # Send initial heartbeat to confirm connection
            yield {"event": "heartbeat", "data": json.dumps({"status": "connected", "seq": 0})}
            _LOGGER.info("Sent initial heartbeat")

            # Start generation in background thread
            gen_thread = threading.Thread(target=run_generation, daemon=True)
            gen_thread.start()

            # Yield events as they come in
            try:
                while not result_holder["done"] or buffer.get_pending_count() > 0:
                    if buffer.should_flush():
                        batch = buffer.flush()
                        if batch:
                            _LOGGER.debug("Yielding batch with events")
                            yield batch
                    else:
                        # Send heartbeat every ~2 seconds.
                        heartbeat_counter += 1
                        if heartbeat_counter >= 100:
                            heartbeat_counter = 0
                            yield {"event": "heartbeat", "data": json.dumps({"status": "generating", "seq": buffer.sequence})}
                    await asyncio.sleep(0.02)

                # Flush any remaining events
                final_batch = buffer.flush()
                if final_batch:
                    _LOGGER.debug("Yielding final batch")
                    yield final_batch

                # Send completion or error event
                if result_holder["error"]:
                    _LOGGER.info("Sending error event: %s", result_holder["error"])
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": result_holder["error"]}),
                    }
                elif result_holder["result"]:
                    # Send final result as the actual staged payload (do not clobber final_artifact).
                    result_data = model_dump(result_holder["result"])
                    result_data["stream_complete"] = True
                    _LOGGER.info("Sending complete event with result")
                    yield {
                        "event": "complete",
                        "data": json.dumps(result_data),
                    }
            except asyncio.CancelledError:
                _LOGGER.info("SSE stream cancelled by client")
                raise

        return EventSourceResponse(event_generator())

    return app


app = create_app()
