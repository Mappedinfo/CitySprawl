from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class TerrainConfig(StrictModel):
    noise_octaves: int = Field(default=5, ge=1, le=8)
    relief_strength: float = Field(default=1.0, gt=0.0, le=5.0)


class HydrologyConfig(StrictModel):
    enable: bool = True
    accum_threshold: float = Field(default=0.015, gt=0.0, lt=1.0)
    min_river_length_m: float = Field(default=120.0, ge=0.0)


class HubsConfig(StrictModel):
    t1_count: int = Field(default=1, ge=1, le=8)
    t2_count: int = Field(default=4, ge=0, le=64)
    t3_count: int = Field(default=20, ge=0, le=512)
    min_distance_m: float = Field(default=120.0, gt=0.0)


class RoadsConfig(StrictModel):
    k_neighbors: int = Field(default=4, ge=2, le=12)
    loop_budget: int = Field(default=3, ge=0, le=64)
    branch_steps: int = Field(default=2, ge=0, le=6)
    slope_penalty: float = Field(default=2.0, ge=0.0, le=50.0)
    river_cross_penalty: float = Field(default=300.0, ge=0.0, le=5000.0)


class NamingConfig(StrictModel):
    provider: str = Field(default="mock", min_length=1)


class GenerateConfig(StrictModel):
    seed: int = 42
    extent_m: float = Field(default=2048.0, gt=100.0)
    grid_resolution: int = Field(default=512, ge=32, le=1024)
    terrain: TerrainConfig = Field(default_factory=TerrainConfig)
    hydrology: HydrologyConfig = Field(default_factory=HydrologyConfig)
    hubs: HubsConfig = Field(default_factory=HubsConfig)
    roads: RoadsConfig = Field(default_factory=RoadsConfig)
    naming: NamingConfig = Field(default_factory=NamingConfig)

    @field_validator("seed")
    @classmethod
    def validate_seed(cls, value: int) -> int:
        if value < 0:
            raise ValueError("seed must be non-negative")
        return value


class Point2D(StrictModel):
    x: float
    y: float


class Polyline2D(StrictModel):
    id: str
    points: List[Point2D]


class TerrainLayer(StrictModel):
    extent_m: float
    resolution: int
    display_resolution: int
    heights: List[List[float]]
    slope_preview: Optional[List[List[float]]] = None


class RiverLine(StrictModel):
    id: str
    points: List[Point2D]
    flow: float
    length_m: float


class HubRecord(StrictModel):
    id: str
    x: float
    y: float
    tier: int
    score: float
    name: Optional[str] = None
    attrs: Dict[str, Any] = Field(default_factory=dict)


class RoadNodeRecord(StrictModel):
    id: str
    x: float
    y: float
    kind: str = "hub"


class RoadEdgeRecord(StrictModel):
    id: str
    u: str
    v: str
    road_class: str
    weight: float
    length_m: float
    river_crossings: int = 0


class RoadNetwork(StrictModel):
    nodes: List[RoadNodeRecord]
    edges: List[RoadEdgeRecord]


class DebugSegment(StrictModel):
    id: str
    a: Point2D
    b: Point2D
    weight: Optional[float] = None


class DebugLayers(StrictModel):
    candidate_edges: List[DebugSegment] = Field(default_factory=list)
    suitability_preview: Optional[List[List[float]]] = None
    accumulation_preview: Optional[List[List[float]]] = None


class Metrics(StrictModel):
    hub_count: int
    road_node_count: int
    road_edge_count: int
    connected: bool
    connectivity_ratio: float
    dead_end_count: int
    duplicate_edge_count: int
    zero_length_edge_count: int
    illegal_intersection_count: int
    bridge_count: int
    river_count: int
    avg_edge_weight: float
    notes: List[str] = Field(default_factory=list)


class ArtifactMeta(StrictModel):
    schema_version: str
    seed: int
    duration_ms: float
    generated_at_utc: str
    config: Dict[str, Any]


class CityArtifact(StrictModel):
    meta: ArtifactMeta
    terrain: TerrainLayer
    rivers: List[RiverLine]
    hubs: List[HubRecord]
    roads: RoadNetwork
    metrics: Metrics
    debug_layers: DebugLayers
