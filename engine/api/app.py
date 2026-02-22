from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from engine.generator import generate_city, generate_city_staged
from engine.models import CityArtifact, GenerateConfig, StagedCityResponse
from engine.pydantic_compat import model_json_schema


def _default_preset() -> GenerateConfig:
    return GenerateConfig()


def _river_valley_preset() -> GenerateConfig:
    return GenerateConfig(
        seed=17,
        extent_m=10000.0,
        terrain={"noise_octaves": 6, "relief_strength": 1.15},
        hydrology={"enable": True, "accum_threshold": 0.012, "min_river_length_m": 1200.0},
        hubs={"t1_count": 1, "t2_count": 5, "t3_count": 18, "min_distance_m": 550.0},
        roads={"k_neighbors": 4, "loop_budget": 4, "branch_steps": 2, "slope_penalty": 1.8, "river_cross_penalty": 260.0},
    )


def _hills_preset() -> GenerateConfig:
    return GenerateConfig(
        seed=103,
        extent_m=10000.0,
        terrain={"noise_octaves": 5, "relief_strength": 1.3},
        hydrology={"enable": True, "accum_threshold": 0.02, "min_river_length_m": 1000.0},
        hubs={"t1_count": 1, "t2_count": 3, "t3_count": 14, "min_distance_m": 700.0},
        roads={"k_neighbors": 3, "loop_budget": 2, "branch_steps": 1, "slope_penalty": 3.0, "river_cross_penalty": 420.0},
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

    return app


app = create_app()
