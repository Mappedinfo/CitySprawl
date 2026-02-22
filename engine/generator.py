from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Sequence

import numpy as np

from engine.analysis import (
    compute_population_potential,
    compute_suitability_and_flood,
    generate_resource_sites,
)
from engine.export.json_export import artifact_to_json
from engine.hubs import generate_hubs
from engine.models import (
    ArtifactMeta,
    CityArtifact,
    DebugLayers,
    DebugSegment,
    GenerateConfig,
    HubRecord,
    Metrics,
    Point2D,
    RiverLine,
    RoadEdgeRecord,
    RoadNetwork,
    RoadNodeRecord,
    StagedCityResponse,
    TerrainLayer,
)
from engine.naming import assign_hub_names, get_toponymy_provider
from engine.pydantic_compat import model_dump
from engine.roads import generate_roads
from engine.staging import build_stages, generate_building_footprints, generate_green_zones_preview
from engine.terrain.generator import generate_terrain_bundle
from engine.terrain.hydrology import downsample_grid
from engine.traffic import assign_edge_flows

SCHEMA_VERSION = "0.1.0"


@dataclass
class _CoreGenerationContext:
    terrain_bundle: Any
    hub_result: Any
    road_result: Any
    names: List[str]
    name_map: Dict[str, str]


def _grid_to_nested_list(grid: np.ndarray, precision: int = 4) -> List[List[float]]:
    rounded = np.round(grid.astype(np.float64), precision)
    return rounded.tolist()


def _point_model(x: float, y: float) -> Point2D:
    return Point2D(x=float(x), y=float(y))


def _generate_core_context(config: GenerateConfig) -> _CoreGenerationContext:
    terrain_bundle = generate_terrain_bundle(
        resolution=config.grid_resolution,
        extent_m=config.extent_m,
        octaves=config.terrain.noise_octaves,
        seed=config.seed,
        relief_strength=config.terrain.relief_strength,
        hydrology_enabled=config.hydrology.enable,
        accum_threshold=config.hydrology.accum_threshold,
        min_river_length_m=config.hydrology.min_river_length_m,
    )

    hub_result = generate_hubs(
        seed=config.seed,
        extent_m=config.extent_m,
        slope=terrain_bundle.slope,
        river_polylines=terrain_bundle.hydrology.river_polylines,
        t1_count=config.hubs.t1_count,
        t2_count=config.hubs.t2_count,
        t3_count=config.hubs.t3_count,
        min_distance_m=config.hubs.min_distance_m,
    )

    road_result = generate_roads(
        hubs=hub_result.hubs,
        extent_m=config.extent_m,
        slope=terrain_bundle.slope,
        river_mask=terrain_bundle.hydrology.river_mask,
        k_neighbors=config.roads.k_neighbors,
        loop_budget=config.roads.loop_budget,
        branch_steps=config.roads.branch_steps,
        slope_penalty=config.roads.slope_penalty,
        river_cross_penalty=config.roads.river_cross_penalty,
        seed=config.seed,
    )

    provider = get_toponymy_provider(config.naming.provider)
    road_edges_for_naming = [
        {"u": e.u, "v": e.v, "road_class": e.road_class, "weight": e.weight}
        for e in road_result.edges
    ]
    names = assign_hub_names(hub_result.hubs, road_edges_for_naming, provider, config.seed)
    name_map = {hub.id: names[i] for i, hub in enumerate(hub_result.hubs)}

    return _CoreGenerationContext(
        terrain_bundle=terrain_bundle,
        hub_result=hub_result,
        road_result=road_result,
        names=names,
        name_map=name_map,
    )


def _build_city_artifact_from_core(
    config: GenerateConfig,
    ctx: _CoreGenerationContext,
    duration_ms: float,
) -> CityArtifact:
    height_preview = downsample_grid(ctx.terrain_bundle.height, max_resolution=128)
    slope_preview = downsample_grid(ctx.terrain_bundle.slope, max_resolution=128)
    accum_preview = downsample_grid(ctx.terrain_bundle.hydrology.accumulation, max_resolution=128)
    if accum_preview.size:
        accum_preview = accum_preview / (float(np.max(accum_preview)) + 1e-9)

    terrain_model = TerrainLayer(
        extent_m=float(config.extent_m),
        resolution=int(config.grid_resolution),
        display_resolution=int(height_preview.shape[0]),
        heights=_grid_to_nested_list(height_preview, precision=4),
        slope_preview=_grid_to_nested_list(slope_preview, precision=5),
    )

    rivers: List[RiverLine] = []
    for river in ctx.terrain_bundle.hydrology.river_polylines:
        points = [Point2D(x=float(p.x), y=float(p.y)) for p in river["points"]]
        rivers.append(
            RiverLine(
                id=str(river["id"]),
                points=points,
                flow=float(river["flow"]),
                length_m=float(river["length_m"]),
            )
        )

    hubs: List[HubRecord] = []
    for hub in ctx.hub_result.hubs:
        hubs.append(
            HubRecord(
                id=hub.id,
                x=float(hub.pos.x),
                y=float(hub.pos.y),
                tier=int(hub.tier),
                score=float(hub.score),
                name=ctx.name_map.get(hub.id),
                attrs={k: float(v) for k, v in hub.attrs.items()},
            )
        )

    road_nodes = [
        RoadNodeRecord(id=n.id, x=float(n.pos.x), y=float(n.pos.y), kind=n.kind)
        for n in ctx.road_result.nodes
    ]
    road_edges = [
        RoadEdgeRecord(
            id=e.id,
            u=e.u,
            v=e.v,
            road_class=e.road_class,
            weight=float(e.weight),
            length_m=float(e.length_m),
            river_crossings=int(e.river_crossings),
        )
        for e in ctx.road_result.edges
    ]
    road_network = RoadNetwork(nodes=road_nodes, edges=road_edges)

    debug_layers = DebugLayers(
        candidate_edges=[
            DebugSegment(
                id=cid,
                a=_point_model(a.x, a.y),
                b=_point_model(b.x, b.y),
                weight=float(weight),
            )
            for cid, a, b, weight in ctx.road_result.candidate_debug[:300]
        ],
        suitability_preview=_grid_to_nested_list(ctx.hub_result.suitability_preview, precision=4),
        accumulation_preview=_grid_to_nested_list(accum_preview, precision=4),
    )

    metric_values = ctx.road_result.metrics
    metrics = Metrics(
        hub_count=len(hubs),
        road_node_count=len(road_nodes),
        road_edge_count=len(road_edges),
        connected=bool(metric_values.get("connected", 0.0) >= 0.5),
        connectivity_ratio=float(metric_values.get("connectivity_ratio", 1.0)),
        dead_end_count=int(metric_values.get("dead_end_count", 0.0)),
        duplicate_edge_count=int(metric_values.get("duplicate_edge_count", 0.0)),
        zero_length_edge_count=int(metric_values.get("zero_length_edge_count", 0.0)),
        illegal_intersection_count=int(metric_values.get("illegal_intersection_count", 0.0)),
        bridge_count=int(metric_values.get("bridge_count", 0.0)),
        river_count=len(rivers),
        avg_edge_weight=float(metric_values.get("avg_edge_weight", 0.0)),
        notes=[
            "Terrain heights are returned as preview grid for web rendering.",
            "Hydrology uses D8 flow accumulation (MVP simplified model).",
        ],
    )

    meta = ArtifactMeta(
        schema_version=SCHEMA_VERSION,
        seed=int(config.seed),
        duration_ms=float(duration_ms),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        config=model_dump(config),
    )

    return CityArtifact(
        meta=meta,
        terrain=terrain_model,
        rivers=rivers,
        hubs=hubs,
        roads=road_network,
        metrics=metrics,
        debug_layers=debug_layers,
    )


def generate_city(config: GenerateConfig) -> CityArtifact:
    t0 = perf_counter()
    ctx = _generate_core_context(config)
    duration_ms = (perf_counter() - t0) * 1000.0
    return _build_city_artifact_from_core(config, ctx, duration_ms)


def generate_city_staged(config: GenerateConfig) -> StagedCityResponse:
    t0 = perf_counter()
    ctx = _generate_core_context(config)

    analysis = compute_suitability_and_flood(
        height=ctx.terrain_bundle.height,
        slope=ctx.terrain_bundle.slope,
        extent_m=config.extent_m,
        river_polylines=ctx.terrain_bundle.hydrology.river_polylines,
        max_resolution=128,
    )
    resource_sites = generate_resource_sites(
        seed=config.seed,
        extent_m=config.extent_m,
        suitability=analysis.suitability,
        flood_risk=analysis.flood_risk,
        height_preview=analysis.height_preview,
        slope_preview=analysis.slope_preview,
        river_polylines=ctx.terrain_bundle.hydrology.river_polylines,
    )
    population_potential = compute_population_potential(
        suitability=analysis.suitability,
        flood_risk=analysis.flood_risk,
        resource_sites=resource_sites,
        extent_m=config.extent_m,
    )

    provisional_artifact = _build_city_artifact_from_core(config, ctx, duration_ms=0.0)
    traffic_result = assign_edge_flows(provisional_artifact.hubs, provisional_artifact.roads)

    building_footprints = generate_building_footprints(
        seed=config.seed,
        extent_m=config.extent_m,
        hubs=provisional_artifact.hubs,
        population_potential_preview=population_potential,
        flood_risk_preview=analysis.flood_risk,
    )
    green_zones_preview = generate_green_zones_preview(
        suitability_preview=analysis.suitability,
        flood_risk_preview=analysis.flood_risk,
        population_potential_preview=population_potential,
    )

    stages = build_stages(
        final_artifact=provisional_artifact,
        suitability_preview=analysis.suitability,
        flood_risk_preview=analysis.flood_risk,
        population_potential_preview=population_potential,
        resource_sites=resource_sites,
        traffic_edge_flows=traffic_result.edge_flows,
        building_footprints=building_footprints,
        green_zones_preview=green_zones_preview,
    )

    duration_ms = (perf_counter() - t0) * 1000.0
    final_artifact = _build_city_artifact_from_core(config, ctx, duration_ms)
    return StagedCityResponse(final_artifact=final_artifact, stages=stages)


def generate_city_json(config: GenerateConfig) -> str:
    return artifact_to_json(generate_city(config))
