from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class TerrainConfig(StrictModel):
    noise_octaves: int = Field(default=5, ge=1, le=8)
    relief_strength: float = Field(default=0.12, gt=0.0, le=5.0)


class HydrologyConfig(StrictModel):
    enable: bool = True
    accum_threshold: float = Field(default=0.015, gt=0.0, lt=1.0)
    min_river_length_m: float = Field(default=1000.0, ge=0.0)
    primary_branch_count_max: int = Field(default=4, ge=0, le=16)
    centerline_smooth_iters: int = Field(default=2, ge=0, le=6)
    width_taper_strength: float = Field(default=0.35, ge=0.0, le=1.0)
    bank_irregularity: float = Field(default=0.08, ge=0.0, le=0.5)


class HubsConfig(StrictModel):
    t1_count: int = Field(default=1, ge=1, le=8)
    t2_count: int = Field(default=4, ge=0, le=64)
    t3_count: int = Field(default=20, ge=0, le=512)
    min_distance_m: float = Field(default=600.0, gt=0.0)


class RoadsConfig(StrictModel):
    @model_validator(mode="before")
    @classmethod
    def strip_deprecated_fields(cls, values: Any) -> Any:
        if isinstance(values, dict):
            deprecated = [k for k in values if k.startswith("tensor_") or k == "use_two_phase_generation"]
            for k in deprecated:
                values.pop(k)
            # Normalize collector_generator legacy aliases to turtle_flow
            if "collector_generator" in values:
                cg = values["collector_generator"]
                if cg in {"classic_turtle", "tensor_streamline"}:
                    values["collector_generator"] = "turtle_flow"
        return values

    k_neighbors: int = Field(default=4, ge=2, le=12)
    loop_budget: int = Field(default=3, ge=0, le=64)
    branch_steps: int = Field(default=2, ge=0, le=6)
    slope_penalty: float = Field(default=2.0, ge=0.0, le=50.0)
    river_cross_penalty: float = Field(default=300.0, ge=0.0, le=5000.0)
    style: str = Field(default="mixed_organic", min_length=1)
    collector_spacing_m: float = Field(default=420.0, gt=10.0, le=5000.0)
    local_spacing_m: float = Field(default=130.0, gt=5.0, le=1000.0)
    collector_jitter: float = Field(default=0.16, ge=0.0, le=1.0)
    local_jitter: float = Field(default=0.22, ge=0.0, le=1.0)
    local_generator: str = Field(default="classic_sprawl", min_length=1)
    local_geometry_mode: str = Field(default="classic_sprawl_rerouted", min_length=1)
    local_reroute_coverage: str = Field(default="selective", min_length=1)
    local_reroute_min_length_m: float = Field(default=70.0, gt=1.0, le=5000.0)
    local_reroute_waypoint_spacing_m: float = Field(default=26.0, gt=1.0, le=500.0)
    local_reroute_max_waypoints: int = Field(default=16, ge=2, le=128)
    local_reroute_corridor_buffer_m: float = Field(default=38.0, gt=1.0, le=500.0)
    local_reroute_block_margin_m: float = Field(default=2.0, ge=0.0, le=100.0)
    local_reroute_slope_penalty_scale: float = Field(default=1.15, ge=0.1, le=10.0)
    local_reroute_river_penalty_scale: float = Field(default=1.35, ge=0.1, le=20.0)
    local_reroute_collector_snap_bias_m: float = Field(default=22.0, ge=0.0, le=200.0)
    local_reroute_smooth_iters: int = Field(default=1, ge=0, le=8)
    local_reroute_simplify_tol_m: float = Field(default=3.0, ge=0.0, le=50.0)
    local_reroute_max_edges_per_city: int = Field(default=180, ge=0, le=5000)
    local_reroute_apply_to_grid_supplement: bool = True
    local_classic_probe_step_m: float = Field(default=18.0, gt=1.0, le=500.0)
    local_classic_seed_spacing_m: float = Field(default=110.0, gt=5.0, le=5000.0)
    local_classic_max_trace_len_m: float = Field(default=6000.0, gt=10.0, le=50000.0)
    local_classic_min_trace_len_m: float = Field(default=48.0, gt=1.0, le=5000.0)
    local_classic_turn_limit_deg: float = Field(default=54.0, ge=1.0, le=180.0)
    local_classic_branch_prob: float = Field(default=0.62, ge=0.0, le=1.0)
    local_classic_continue_prob: float = Field(default=1.0, ge=0.0, le=1.0)
    local_classic_culdesac_prob: float = Field(default=0.0, ge=0.0, le=1.0)
    local_classic_max_segments_per_block: int = Field(default=28, ge=1, le=5000)
    local_classic_max_road_distance_m: float = Field(default=500.0, gt=0.0, le=50000.0)
    local_classic_depth_decay_power: float = Field(default=1.5, ge=0.5, le=5.0)
    local_community_seed_count_per_block: int = Field(default=3, ge=1, le=32)
    local_community_spine_prob: float = Field(default=0.28, ge=0.0, le=1.0)
    local_arterial_setback_weight: float = Field(default=0.5, ge=0.0, le=5.0)
    local_collector_follow_weight: float = Field(default=0.9, ge=0.0, le=5.0)
    river_setback_m: float = Field(default=18.0, ge=0.0, le=500.0)
    minor_bridge_budget: int = Field(default=4, ge=0, le=64)
    max_local_block_area_m2: float = Field(default=180000.0, ge=100.0)
    collector_generator: str = Field(default="turtle_flow", min_length=1)
    classic_probe_step_m: float = Field(default=24.0, gt=1.0, le=500.0)
    classic_seed_spacing_m: float = Field(default=120.0, gt=5.0, le=5000.0)
    classic_max_trace_len_m: float = Field(default=5000.0, gt=10.0, le=50000.0)
    classic_min_trace_len_m: float = Field(default=200.0, gt=1.0, le=5000.0)
    classic_turn_limit_deg: float = Field(default=38.0, ge=1.0, le=180.0)
    classic_branch_prob: float = Field(default=1.0, ge=0.0, le=1.0)
    classic_continue_prob: float = Field(default=1.0, ge=0.0, le=1.0)
    classic_culdesac_prob: float = Field(default=0.18, ge=0.0, le=1.0)
    classic_max_queue_size: int = Field(default=2000, ge=10, le=100000)
    classic_max_segments: int = Field(default=1200, ge=1, le=100000)
    classic_max_arterial_distance_m: float = Field(default=800.0, gt=0.0, le=50000.0)
    classic_depth_decay_power: float = Field(default=1.5, ge=0.5, le=5.0)
    slope_straight_threshold_deg: float = Field(default=5.0, ge=0.0, le=89.0)
    slope_serpentine_threshold_deg: float = Field(default=15.0, ge=0.0, le=89.0)
    slope_hard_limit_deg: float = Field(default=22.0, ge=0.0, le=89.0)
    contour_follow_weight: float = Field(default=0.9, ge=0.0, le=5.0)
    arterial_align_weight: float = Field(default=0.6, ge=0.0, le=5.0)
    hub_seek_weight: float = Field(default=0.25, ge=0.0, le=5.0)
    river_snap_dist_m: float = Field(default=28.0, ge=0.0, le=500.0)
    river_parallel_bias_weight: float = Field(default=1.0, ge=0.0, le=5.0)
    river_avoid_weight: float = Field(default=1.2, ge=0.0, le=20.0)
    intersection_snap_radius_m: float = Field(default=12.0, ge=0.0, le=500.0)
    intersection_t_junction_radius_m: float = Field(default=18.0, ge=0.0, le=500.0)
    intersection_split_tolerance_m: float = Field(default=1.5, ge=0.0, le=100.0)
    min_dangle_length_m: float = Field(default=15.0, ge=0.0, le=1000.0)
    syntax_enable: bool = True
    syntax_choice_radius_hops: int = Field(default=10, ge=1, le=256)
    syntax_prune_low_choice_collectors: bool = False
    syntax_prune_quantile: float = Field(default=0.15, ge=0.0, le=0.99)
    enable_legacy_branches: bool = False
    local_minor_run_hard_cap_m: float = Field(default=6000.0, gt=10.0, le=50000.0)
    local_sub_branch_interval_min_m: float = Field(default=200.0, gt=10.0, le=5000.0)
    local_sub_branch_interval_max_m: float = Field(default=400.0, gt=10.0, le=5000.0)
    local_sub_branch_max_depth: int = Field(default=2, ge=0, le=10)
    local_sub_branch_connector_seek_radius_m: float = Field(default=1200.0, gt=10.0, le=10000.0)
    # Major Local trace logging configuration
    major_local_enable_trace_logging: bool = False
    major_local_trace_log_step_details: bool = True
    major_local_trace_log_output_path: Optional[str] = None
    major_local_trace_log_include_rejected: bool = True
    major_local_trace_log_max_traces: int = Field(default=0, ge=0)


class QualityConfig(StrictModel):
    profile: str = Field(default="balanced", min_length=1)
    time_budget_ms: int = Field(default=15000, ge=500, le=600000)


class ParcelsConfig(StrictModel):
    enable: bool = True
    residential_target_area_m2: float = Field(default=1800.0, ge=50.0)
    mixed_target_area_m2: float = Field(default=2600.0, ge=50.0)
    min_frontage_m: float = Field(default=10.0, ge=1.0)
    min_depth_m: float = Field(default=12.0, ge=1.0)
    parcel_local_morphology_coupling: bool = True
    parcel_culdesac_frontage_relaxation: float = Field(default=0.18, ge=0.0, le=0.8)
    parcel_local_depth_bias: float = Field(default=0.10, ge=-0.5, le=1.0)
    parcel_curvilinear_split_bias: float = Field(default=0.20, ge=0.0, le=1.0)


class NamingConfig(StrictModel):
    provider: str = Field(default="mock", min_length=1)


class GenerateConfig(StrictModel):
    seed: int = 42
    extent_m: float = Field(default=10000.0, gt=100.0)
    grid_resolution: int = Field(default=512, ge=32, le=1024)
    terrain: TerrainConfig = Field(default_factory=TerrainConfig)
    hydrology: HydrologyConfig = Field(default_factory=HydrologyConfig)
    hubs: HubsConfig = Field(default_factory=HubsConfig)
    roads: RoadsConfig = Field(default_factory=RoadsConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    parcels: ParcelsConfig = Field(default_factory=ParcelsConfig)
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


class Polygon2D(StrictModel):
    id: str
    points: List[Point2D]


class ContourLine(Polyline2D):
    elevation_norm: float = 0.0


class RiverAreaPolygon(Polygon2D):
    flow: float = 0.0
    width_mean_m: float = 0.0
    is_main_stem: bool = False
    source_river_id: Optional[str] = None


class PedestrianPath(Polyline2D):
    width_m: float = 3.0
    parent_block_id: Optional[str] = None


class LandBlock(Polygon2D):
    block_class: str = "unclassified"
    area_m2: float = 0.0
    parent_id: Optional[str] = None


class ParcelLot(Polygon2D):
    parcel_class: str = "unclassified"
    area_m2: float = 0.0
    parent_block_id: Optional[str] = None


class TerrainLayer(StrictModel):
    extent_m: float
    resolution: int
    display_resolution: int
    heights: List[List[float]]
    slope_preview: Optional[List[List[float]]] = None
    terrain_class_preview: Optional[List[List[int]]] = None
    hillshade_preview: Optional[List[List[float]]] = None
    contours: List[ContourLine] = Field(default_factory=list)


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
    width_m: float = 8.0
    render_order: int = 1
    path_points: Optional[List[Point2D]] = None
    continuity_id: Optional[str] = None
    parent_continuity_id: Optional[str] = None
    segment_order: Optional[int] = None


class RoadNetwork(StrictModel):
    nodes: List[RoadNodeRecord]
    edges: List[RoadEdgeRecord]


class ResourceSite(StrictModel):
    id: str
    x: float
    y: float
    kind: str
    quality: float
    influence_radius_m: float


class TrafficEdgeFlow(StrictModel):
    edge_id: str
    flow: float
    capacity: float
    congestion_ratio: float
    road_class: str


class BuildingFootprint(StrictModel):
    id: str
    points: List[Point2D]
    height_hint: float = 1.0


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
    river_coverage_ratio: float = 0.0
    main_river_length_m: float = 0.0
    river_area_m2: float = 0.0
    river_area_clipped_ratio: Optional[float] = None
    river_mainstem_count: int = 0
    visual_envelope_area_ratio: Optional[float] = None
    avg_edge_weight: float
    road_edge_count_by_class: Dict[str, int] = Field(default_factory=dict)
    parcel_count: int = 0
    median_parcel_area_m2: float = 0.0
    intersection_snap_to_node_count: int = 0
    intersection_t_junction_count: int = 0
    intersection_t_split_target_count: int = 0
    intersection_crossing_split_count: int = 0
    intersection_pruned_dangle_count: int = 0
    major_local_classic_riverfront_seed_count: int = 0
    major_local_classic_riverfront_trace_count: int = 0
    major_local_classic_arterial_t_attach_count: int = 0
    major_local_classic_network_attach_fallback_count: int = 0
    major_local_classic_failed_arterial_attach_count: int = 0
    local_culdesac_edge_count_pre_topology: int = 0
    local_culdesac_edge_count_final: int = 0
    local_culdesac_preserved_ratio: float = 0.0
    local_reroute_candidate_count: int = 0
    local_reroute_applied_count: int = 0
    local_reroute_fallback_count: int = 0
    local_reroute_grid_supplement_applied_count: int = 0
    local_two_point_edge_count: int = 0
    local_two_point_edge_ratio: float = 0.0
    local_reroute_avg_path_points: float = 0.0
    local_reroute_avg_length_gain_ratio: float = 0.0
    local_buildable_area_m2: Optional[float] = None
    local_coverage_radius_m: Optional[float] = None
    local_coverage_ratio: Optional[float] = None
    local_uncovered_area_m2: Optional[float] = None
    local_coverage_supplement_added_count: Optional[int] = None
    local_frontier_supplement_added_count: Optional[int] = None
    local_grid_supplement_budget: Optional[int] = None
    local_grid_supplement_added_count: Optional[int] = None
    local_grid_supplement_used_ratio: Optional[float] = None
    local_classic_stop_near_network_count: Optional[int] = None
    local_classic_stop_block_exit_count: Optional[int] = None
    local_classic_stop_stochastic_stop_count: Optional[int] = None
    local_classic_stop_road_too_far_count: Optional[int] = None
    local_classic_stop_river_blocked_count: Optional[int] = None
    local_classic_stop_span_cap_count: Optional[int] = None
    local_classic_contact_opposing_count: Optional[int] = None
    local_classic_contact_parallel_count: Optional[int] = None
    local_classic_contact_perpendicular_continue_count: Optional[int] = None
    local_classic_contact_oblique_continue_count: Optional[int] = None
    minor_local_run_count: Optional[int] = None
    minor_local_run_generator_enabled: Optional[float] = None
    minor_local_continuity_group_count: Optional[int] = None
    minor_local_edges_with_continuity_count: Optional[int] = None
    generation_profile: str = "balanced"
    degraded_mode: bool = False
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
    river_areas: List[RiverAreaPolygon] = Field(default_factory=list)
    visual_envelope: Optional[Polygon2D] = None
    hubs: List[HubRecord]
    roads: RoadNetwork
    pedestrian_paths: List[PedestrianPath] = Field(default_factory=list)
    blocks: List[LandBlock] = Field(default_factory=list)
    parcels: List[ParcelLot] = Field(default_factory=list)
    metrics: Metrics
    debug_layers: DebugLayers


class StageCaption(StrictModel):
    text: str
    text_zh: Optional[str] = None


class StageLayersSnapshot(StrictModel):
    terrain_class_preview: Optional[List[List[int]]] = None
    hillshade_preview: Optional[List[List[float]]] = None
    contour_lines: List[ContourLine] = Field(default_factory=list)
    river_area_polygons: List[RiverAreaPolygon] = Field(default_factory=list)
    visual_envelope: Optional[Polygon2D] = None
    suitability_preview: Optional[List[List[float]]] = None
    flood_risk_preview: Optional[List[List[float]]] = None
    population_potential_preview: Optional[List[List[float]]] = None
    resource_sites: List[ResourceSite] = Field(default_factory=list)
    traffic_edge_flows: List[TrafficEdgeFlow] = Field(default_factory=list)
    pedestrian_paths: List[PedestrianPath] = Field(default_factory=list)
    land_blocks: List[LandBlock] = Field(default_factory=list)
    parcel_lots: List[ParcelLot] = Field(default_factory=list)
    building_footprints: List[BuildingFootprint] = Field(default_factory=list)
    green_zones_preview: Optional[List[List[float]]] = None


class StageArtifact(StrictModel):
    stage_id: str
    title: str
    title_zh: str
    subtitle: str
    subtitle_zh: str
    timestamp_ms: int
    visible_layers: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    caption: Optional[StageCaption] = None
    layers: StageLayersSnapshot = Field(default_factory=StageLayersSnapshot)


class StagedCityResponse(StrictModel):
    final_artifact: CityArtifact
    stages: List[StageArtifact]
