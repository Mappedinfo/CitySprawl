from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
from shapely.geometry import MultiPoint, Polygon as ShapelyPolygon, box
from shapely.geometry.base import BaseGeometry

from engine.analysis import (
    compute_population_potential,
    compute_suitability_and_flood,
    generate_resource_sites,
)
from engine.blocks import (
    classify_blocks_and_parcels,
    extract_macro_blocks,
    generate_pedestrian_paths_and_parcels,
)
from engine.blocks.extraction import river_union_geometry
from engine.export.json_export import artifact_to_json
from engine.hubs import generate_hubs
from engine.models import (
    ArtifactMeta,
    CityArtifact,
    DebugLayers,
    DebugSegment,
    GenerateConfig,
    HubRecord,
    LandBlock,
    Metrics,
    ParcelLot,
    PedestrianPath,
    Point2D,
    Polygon2D,
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
from engine.roads.pedestrian import PEDESTRIAN_WIDTH_M
from engine.staging import build_stages, generate_building_footprints, generate_green_zones_preview
from engine.terrain.classification import TerrainVisualSurfaces, compute_terrain_classification
from engine.terrain.contours import extract_contour_lines
from engine.terrain.generator import generate_terrain_bundle
from engine.terrain.hydrology import compute_hydrology, downsample_grid
from engine.terrain.river_area import build_river_area_polygons
from engine.traffic import assign_edge_flows

SCHEMA_VERSION = "0.1.0"


@dataclass
class _CoreGenerationContext:
    terrain_bundle: Any
    hub_result: Any
    road_result: Any
    names: List[str]
    name_map: Dict[str, str]
    selected_rivers_raw: List[Dict[str, object]]
    terrain_visuals: TerrainVisualSurfaces
    contour_lines: List[Any]
    river_areas: List[Any]
    river_coverage_ratio: float
    river_area_m2: float
    main_river_length_m: float
    river_area_clipped_ratio: float


def _grid_to_nested_list(grid: np.ndarray, precision: int = 4) -> List[List[float]]:
    rounded = np.round(grid.astype(np.float64), precision)
    return rounded.tolist()


def _grid_to_nested_int_list(grid: np.ndarray) -> List[List[int]]:
    return grid.astype(np.int64).tolist()


def _point_model(x: float, y: float) -> Point2D:
    return Point2D(x=float(x), y=float(y))


def _river_area_stats(
    extent_m: float,
    selected_rivers_raw: Sequence[Dict[str, object]],
    river_areas: Sequence[Any],
) -> Tuple[float, float, float]:
    area_union = river_union_geometry(river_areas)
    river_area_m2 = float(getattr(area_union, "area", 0.0) or 0.0)
    denom = max(float(extent_m) * float(extent_m), 1e-9)
    coverage = float(river_area_m2 / denom)
    main_len = 0.0
    if selected_rivers_raw:
        main = max(selected_rivers_raw, key=lambda r: float(r.get("flow", 0.0)))
        main_len = float(main.get("length_m", 0.0))
    return coverage, river_area_m2, main_len


def _polygon2d_from_shapely(poly: BaseGeometry, poly_id: str) -> Polygon2D | None:
    if poly.is_empty:
        return None
    geom = poly
    if not getattr(geom, "is_valid", True):
        geom = geom.buffer(0)
    if geom.is_empty:
        return None
    if geom.geom_type == "MultiPolygon":
        geoms = list(getattr(geom, "geoms", []))
        if not geoms:
            return None
        geom = max(geoms, key=lambda g: float(getattr(g, "area", 0.0) or 0.0))
    if geom.geom_type != "Polygon":
        return None
    coords = list(geom.exterior.coords)
    if len(coords) < 4:
        return None
    return Polygon2D(id=poly_id, points=[Point2D(x=float(x), y=float(y)) for x, y in coords[:-1]])


def _build_visual_envelope(
    extent_m: float,
    hubs: Sequence[HubRecord],
    road_network: RoadNetwork,
) -> tuple[Polygon2D, float]:
    boundary = box(0.0, 0.0, float(extent_m), float(extent_m))
    node_lookup = {n.id: n for n in road_network.nodes}
    pts: list[tuple[float, float]] = []
    for hub in hubs:
        pts.append((float(hub.x), float(hub.y)))
    for edge in road_network.edges:
        path_points = edge.path_points or []
        if path_points:
            pts.extend((float(p.x), float(p.y)) for p in path_points)
            continue
        u = node_lookup.get(edge.u)
        v = node_lookup.get(edge.v)
        if u is not None:
            pts.append((float(u.x), float(u.y)))
        if v is not None:
            pts.append((float(v.x), float(v.y)))

    if len(pts) < 3:
        fallback = _polygon2d_from_shapely(boundary, "visual-envelope")
        assert fallback is not None
        return fallback, 1.0

    hull = MultiPoint(pts).convex_hull
    geom = hull
    if geom.geom_type != "Polygon":
        geom = geom.buffer(300.0)
    else:
        geom = geom.buffer(300.0, join_style=2)
    geom = geom.intersection(boundary)
    geom = geom.buffer(0)
    model = _polygon2d_from_shapely(geom, "visual-envelope")
    if model is None:
        fallback = _polygon2d_from_shapely(boundary, "visual-envelope")
        assert fallback is not None
        return fallback, 1.0
    area_ratio = max(0.0, min(1.0, float(getattr(geom, "area", 0.0) or 0.0) / max(extent_m * extent_m, 1e-9)))
    return model, area_ratio


def _build_adaptive_rivers(config: GenerateConfig, terrain_bundle: Any) -> Tuple[List[Dict[str, object]], List[Any], float, float, float, float]:
    width_scale = 1.0
    hydrology = terrain_bundle.hydrology
    selected_rivers_raw, river_areas, river_meta = build_river_area_polygons(
        hydrology.river_polylines,
        max_branches=2,
        width_scale=width_scale,
        clip_extent_m=float(config.extent_m),
        min_area_m2=5_000.0,
        return_meta=True,
    )
    coverage, river_area_m2, main_len = _river_area_stats(config.extent_m, selected_rivers_raw, river_areas)
    pre_clip_area = float(river_meta.get("pre_clip_area_m2", 0.0))
    clipped_ratio = float(river_meta.get("post_clip_area_m2", 0.0) / pre_clip_area) if pre_clip_area > 1e-9 else 1.0

    target_ratio = 0.20
    min_ratio = 0.10
    max_ratio = 0.30
    accum_threshold = float(config.hydrology.accum_threshold)

    for _ in range(4):
        if min_ratio <= coverage <= max_ratio and river_areas:
            break
        if (not river_areas or coverage < min_ratio) and config.hydrology.enable:
            accum_threshold = max(1e-5, accum_threshold * 0.75)
            hydrology = compute_hydrology(
                height=terrain_bundle.height,
                extent_m=config.extent_m,
                enabled=config.hydrology.enable,
                accum_threshold=accum_threshold,
                min_river_length_m=config.hydrology.min_river_length_m,
            )
            terrain_bundle.hydrology = hydrology
        # Width-scale adaptation (soft constraint)
        coverage_safe = max(coverage, 1e-6)
        width_scale = min(200.0, max(0.6, width_scale * (target_ratio / coverage_safe)))
        if coverage > max_ratio:
            # If too large, also raise threshold slightly to thin tributaries before retry.
            accum_threshold = min(0.25, accum_threshold * 1.15)
            if config.hydrology.enable:
                hydrology = compute_hydrology(
                    height=terrain_bundle.height,
                    extent_m=config.extent_m,
                    enabled=config.hydrology.enable,
                    accum_threshold=accum_threshold,
                    min_river_length_m=config.hydrology.min_river_length_m,
                )
                terrain_bundle.hydrology = hydrology
        selected_rivers_raw, river_areas, river_meta = build_river_area_polygons(
            hydrology.river_polylines,
            max_branches=2,
            width_scale=width_scale,
            clip_extent_m=float(config.extent_m),
            min_area_m2=5_000.0,
            return_meta=True,
        )
        coverage, river_area_m2, main_len = _river_area_stats(config.extent_m, selected_rivers_raw, river_areas)
        pre_clip_area = float(river_meta.get("pre_clip_area_m2", 0.0))
        clipped_ratio = float(river_meta.get("post_clip_area_m2", 0.0) / pre_clip_area) if pre_clip_area > 1e-9 else 1.0

    return selected_rivers_raw, river_areas, coverage, river_area_m2, main_len, clipped_ratio


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

    selected_rivers_raw, river_areas, river_coverage_ratio, river_area_m2, main_river_length_m, river_area_clipped_ratio = _build_adaptive_rivers(
        config,
        terrain_bundle,
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
    terrain_visuals = compute_terrain_classification(
        height=terrain_bundle.height,
        slope=terrain_bundle.slope,
        extent_m=config.extent_m,
        river_polylines=selected_rivers_raw if selected_rivers_raw else terrain_bundle.hydrology.river_polylines,
        max_resolution=128,
    )
    contour_lines = extract_contour_lines(
        terrain_bundle.height,
        extent_m=config.extent_m,
        max_resolution=128,
        contour_count=12,
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
        selected_rivers_raw=selected_rivers_raw,
        terrain_visuals=terrain_visuals,
        contour_lines=contour_lines,
        river_areas=river_areas,
        river_coverage_ratio=river_coverage_ratio,
        river_area_m2=river_area_m2,
        main_river_length_m=main_river_length_m,
        river_area_clipped_ratio=river_area_clipped_ratio,
    )


def _build_city_artifact_from_core(
    config: GenerateConfig,
    ctx: _CoreGenerationContext,
    duration_ms: float,
) -> CityArtifact:
    height_preview = ctx.terrain_visuals.height_preview
    slope_preview = ctx.terrain_visuals.slope_preview
    accum_preview = downsample_grid(ctx.terrain_bundle.hydrology.accumulation, max_resolution=128)
    if accum_preview.size:
        accum_preview = accum_preview / (float(np.max(accum_preview)) + 1e-9)

    terrain_model = TerrainLayer(
        extent_m=float(config.extent_m),
        resolution=int(config.grid_resolution),
        display_resolution=int(height_preview.shape[0]),
        heights=_grid_to_nested_list(height_preview, precision=4),
        slope_preview=_grid_to_nested_list(slope_preview, precision=5),
        terrain_class_preview=_grid_to_nested_int_list(ctx.terrain_visuals.terrain_class_preview),
        hillshade_preview=_grid_to_nested_list(ctx.terrain_visuals.hillshade_preview, precision=5),
        contours=list(ctx.contour_lines),
    )

    rivers: List[RiverLine] = []
    source_rivers = ctx.selected_rivers_raw if ctx.selected_rivers_raw else ctx.terrain_bundle.hydrology.river_polylines
    for river in source_rivers:
        points = []
        for p in river["points"]:
            if isinstance(p, tuple) or isinstance(p, list):
                points.append(Point2D(x=float(p[0]), y=float(p[1])))
            elif isinstance(p, dict):
                points.append(Point2D(x=float(p["x"]), y=float(p["y"])))
            else:
                points.append(Point2D(x=float(p.x), y=float(p.y)))
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
            width_m=float(getattr(e, "width_m", 8.0)),
            render_order=int(getattr(e, "render_order", 1)),
            path_points=[Point2D(x=float(p.x), y=float(p.y)) for p in getattr(e, "path_points", [])] or None,
        )
        for e in ctx.road_result.edges
    ]
    road_network = RoadNetwork(nodes=road_nodes, edges=road_edges)
    visual_envelope, visual_envelope_area_ratio = _build_visual_envelope(float(config.extent_m), hubs, road_network)

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
    metric_notes = [
        "Terrain heights are returned as preview grid for web rendering.",
        "Hydrology uses D8 flow accumulation (MVP simplified model).",
    ]
    if ctx.river_area_clipped_ratio < 0.6:
        metric_notes.append("River buffer geometry was heavily clipped to study boundary.")

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
        river_coverage_ratio=float(ctx.river_coverage_ratio),
        main_river_length_m=float(ctx.main_river_length_m),
        river_area_m2=float(ctx.river_area_m2),
        river_area_clipped_ratio=float(ctx.river_area_clipped_ratio),
        visual_envelope_area_ratio=float(visual_envelope_area_ratio),
        avg_edge_weight=float(metric_values.get("avg_edge_weight", 0.0)),
        notes=metric_notes,
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
        river_areas=list(ctx.river_areas),
        visual_envelope=visual_envelope,
        hubs=hubs,
        roads=road_network,
        metrics=metrics,
        debug_layers=debug_layers,
    )


def _attach_land_use_layers(
    artifact: CityArtifact,
    extent_m: float,
    resource_sites: Sequence[Any],
    flood_risk_preview: np.ndarray | None,
) -> Tuple[List[PedestrianPath], List[LandBlock], List[ParcelLot]]:
    extraction = extract_macro_blocks(extent_m, artifact.roads, artifact.river_areas)
    parcelization = generate_pedestrian_paths_and_parcels(
        extraction.macro_blocks,
        pedestrian_width_m=PEDESTRIAN_WIDTH_M,
    )
    blocks, parcels = classify_blocks_and_parcels(
        extent_m=extent_m,
        extraction=extraction,
        parcel_polygons_by_block=parcelization.parcel_polygons_by_block,
        hubs=artifact.hubs,
        road_network=artifact.roads,
        river_areas=artifact.river_areas,
        resource_sites=resource_sites,
        flood_risk_preview=flood_risk_preview,
    )
    artifact.pedestrian_paths = list(parcelization.pedestrian_paths)
    artifact.blocks = list(blocks)
    artifact.parcels = list(parcels)
    return artifact.pedestrian_paths, artifact.blocks, artifact.parcels


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

    final_artifact = _build_city_artifact_from_core(config, ctx, duration_ms=0.0)
    traffic_result = assign_edge_flows(final_artifact.hubs, final_artifact.roads)

    building_footprints = generate_building_footprints(
        seed=config.seed,
        extent_m=config.extent_m,
        hubs=final_artifact.hubs,
        population_potential_preview=population_potential,
        flood_risk_preview=analysis.flood_risk,
    )
    green_zones_preview = generate_green_zones_preview(
        suitability_preview=analysis.suitability,
        flood_risk_preview=analysis.flood_risk,
        population_potential_preview=population_potential,
    )

    pedestrian_paths, land_blocks, parcel_lots = _attach_land_use_layers(
        final_artifact,
        extent_m=config.extent_m,
        resource_sites=resource_sites,
        flood_risk_preview=analysis.flood_risk,
    )

    duration_ms = (perf_counter() - t0) * 1000.0
    final_artifact.meta.duration_ms = float(duration_ms)
    final_artifact.meta.generated_at_utc = datetime.now(timezone.utc).isoformat()

    stages = build_stages(
        final_artifact=final_artifact,
        suitability_preview=analysis.suitability,
        flood_risk_preview=analysis.flood_risk,
        population_potential_preview=population_potential,
        resource_sites=resource_sites,
        traffic_edge_flows=traffic_result.edge_flows,
        building_footprints=building_footprints,
        green_zones_preview=green_zones_preview,
        terrain_class_preview=ctx.terrain_visuals.terrain_class_preview,
        hillshade_preview=ctx.terrain_visuals.hillshade_preview,
        contour_lines=ctx.contour_lines,
        river_area_polygons=final_artifact.river_areas,
        pedestrian_paths=pedestrian_paths,
        land_blocks=land_blocks,
        parcel_lots=parcel_lots,
    )
    return StagedCityResponse(final_artifact=final_artifact, stages=stages)


def generate_city_json(config: GenerateConfig) -> str:
    return artifact_to_json(generate_city(config))
