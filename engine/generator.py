from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Dict, List, Sequence, Tuple

import numpy as np
from shapely.geometry import MultiPoint, Polygon as ShapelyPolygon, box
from shapely.geometry.base import BaseGeometry

from engine.analysis import (
    compute_population_potential,
    compute_suitability_and_flood,
    generate_resource_sites,
)
from engine.blocks import (
    BlockExtractionConfig,
    FrontageParcelConfig,
    classify_blocks_and_parcels,
    extract_macro_blocks,
    generate_frontage_parcels,
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
ProgressCallback = Callable[[str, float, str], None]
StreamCallback = Callable[[Dict[str, Any]], None]


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


@dataclass
class _AnalysisAndLandLayers:
    analysis: Any
    resource_sites: List[Any]
    population_potential: np.ndarray
    traffic_result: Any
    building_footprints: List[Any]
    green_zones_preview: np.ndarray
    pedestrian_paths: List[PedestrianPath]
    land_blocks: List[LandBlock]
    parcel_lots: List[ParcelLot]


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


def _emit_progress(progress_cb: ProgressCallback | None, phase: str, progress: float, message: str) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(str(phase), float(max(0.0, min(1.0, progress))), str(message))
    except Exception:
        # progress reporting must never break generation
        return


def _progress_subrange(progress_cb: ProgressCallback | None, start: float, end: float) -> ProgressCallback | None:
    if progress_cb is None:
        return None
    s = float(start)
    e = float(end)

    def _cb(phase: str, progress: float, message: str) -> None:
        p = s + (e - s) * float(max(0.0, min(1.0, progress)))
        _emit_progress(progress_cb, phase, p, message)

    return _cb


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
        max_branches=int(getattr(config.hydrology, "primary_branch_count_max", 2)),
        width_scale=width_scale,
        clip_extent_m=float(config.extent_m),
        min_area_m2=5_000.0,
        centerline_smooth_iters=int(getattr(config.hydrology, "centerline_smooth_iters", 0)),
        width_taper_strength=float(getattr(config.hydrology, "width_taper_strength", 0.0)),
        bank_irregularity=float(getattr(config.hydrology, "bank_irregularity", 0.0)),
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
            max_branches=int(getattr(config.hydrology, "primary_branch_count_max", 2)),
            width_scale=width_scale,
            clip_extent_m=float(config.extent_m),
            min_area_m2=5_000.0,
            centerline_smooth_iters=int(getattr(config.hydrology, "centerline_smooth_iters", 0)),
            width_taper_strength=float(getattr(config.hydrology, "width_taper_strength", 0.0)),
            bank_irregularity=float(getattr(config.hydrology, "bank_irregularity", 0.0)),
            return_meta=True,
        )
        coverage, river_area_m2, main_len = _river_area_stats(config.extent_m, selected_rivers_raw, river_areas)
        pre_clip_area = float(river_meta.get("pre_clip_area_m2", 0.0))
        clipped_ratio = float(river_meta.get("post_clip_area_m2", 0.0) / pre_clip_area) if pre_clip_area > 1e-9 else 1.0

    return selected_rivers_raw, river_areas, coverage, river_area_m2, main_len, clipped_ratio


def _generate_core_context(
    config: GenerateConfig,
    *,
    progress_cb: ProgressCallback | None = None,
    stream_cb: StreamCallback | None = None,
) -> _CoreGenerationContext:
    _emit_progress(progress_cb, "terrain", 0.02, "Generating terrain and base hydrology")
    terrain_bundle = generate_terrain_bundle(
        resolution=config.grid_resolution,
        extent_m=config.extent_m,
        octaves=config.terrain.noise_octaves,
        seed=config.seed,
        relief_strength=config.terrain.relief_strength,
        hydrology_enabled=config.hydrology.enable,
        accum_threshold=config.hydrology.accum_threshold,
        min_river_length_m=config.hydrology.min_river_length_m,
        stream_cb=stream_cb,
    )
    _emit_progress(progress_cb, "rivers", 0.16, "Selecting and shaping river geometry")

    selected_rivers_raw, river_areas, river_coverage_ratio, river_area_m2, main_river_length_m, river_area_clipped_ratio = _build_adaptive_rivers(
        config,
        terrain_bundle,
    )
    # Emit river centerline events for real-time visualization
    if stream_cb and selected_rivers_raw:
        for river in selected_rivers_raw:
            try:
                pts = river.get("points", [])
                centerline = []
                for p in pts[:200]:
                    if isinstance(p, tuple) or isinstance(p, list):
                        centerline.append({"x": float(p[0]), "y": float(p[1])})
                    elif isinstance(p, dict):
                        centerline.append({"x": float(p["x"]), "y": float(p["y"])})
                    else:
                        centerline.append({"x": float(p.x), "y": float(p.y)})
                stream_cb({
                    "event_type": "river_progress",
                    "data": {
                        "river_id": str(river.get("id", "")),
                        "centerline": centerline,
                        "flow": float(river.get("flow", 0)),
                    },
                })
            except Exception:
                pass
    _emit_progress(progress_cb, "hubs", 0.28, "Sampling hub centers")

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
    # Emit hub placement events for real-time visualization
    if stream_cb and hub_result:
        for hub in hub_result.hubs:
            try:
                stream_cb({
                    "event_type": "road_node_added",
                    "data": {"id": hub.id, "x": float(hub.pos.x), "y": float(hub.pos.y), "kind": f"hub_t{hub.tier}"},
                })
            except Exception:
                pass
    _emit_progress(progress_cb, "roads", 0.36, "Generating road network")

    roads_cfg = config.roads
    collector_generator_value = str(getattr(roads_cfg, "collector_generator", "classic_turtle"))
    # Legacy compatibility: map legacy tensor collector parameters onto the classic collector generator
    # when callers still request the deprecated backend name.
    classic_probe_step_m = float(getattr(roads_cfg, "classic_probe_step_m", 24.0))
    classic_seed_spacing_m = float(getattr(roads_cfg, "classic_seed_spacing_m", 260.0))
    classic_max_trace_len_m = float(getattr(roads_cfg, "classic_max_trace_len_m", 1800.0))
    classic_min_trace_len_m = float(getattr(roads_cfg, "classic_min_trace_len_m", 120.0))
    classic_turn_limit_deg = float(getattr(roads_cfg, "classic_turn_limit_deg", 38.0))
    if collector_generator_value.lower() == "tensor_streamline":
        classic_probe_step_m = float(getattr(roads_cfg, "tensor_step_m", classic_probe_step_m))
        classic_seed_spacing_m = float(getattr(roads_cfg, "tensor_seed_spacing_m", classic_seed_spacing_m))
        classic_max_trace_len_m = float(getattr(roads_cfg, "tensor_max_trace_len_m", classic_max_trace_len_m))
        classic_min_trace_len_m = float(getattr(roads_cfg, "tensor_min_trace_len_m", classic_min_trace_len_m))
        classic_turn_limit_deg = float(getattr(roads_cfg, "tensor_turn_limit_deg", classic_turn_limit_deg))

    road_result = generate_roads(
        hubs=hub_result.hubs,
        extent_m=config.extent_m,
        height=terrain_bundle.height,
        slope=terrain_bundle.slope,
        river_mask=terrain_bundle.hydrology.river_mask,
        k_neighbors=roads_cfg.k_neighbors,
        loop_budget=roads_cfg.loop_budget,
        branch_steps=roads_cfg.branch_steps,
        slope_penalty=roads_cfg.slope_penalty,
        river_cross_penalty=roads_cfg.river_cross_penalty,
        seed=config.seed,
        road_style=roads_cfg.style,
        collector_spacing_m=roads_cfg.collector_spacing_m,
        local_spacing_m=roads_cfg.local_spacing_m,
        collector_jitter=roads_cfg.collector_jitter,
        local_jitter=roads_cfg.local_jitter,
        local_generator=roads_cfg.local_generator,
        local_geometry_mode=roads_cfg.local_geometry_mode,
        local_reroute_coverage=roads_cfg.local_reroute_coverage,
        local_reroute_min_length_m=roads_cfg.local_reroute_min_length_m,
        local_reroute_waypoint_spacing_m=roads_cfg.local_reroute_waypoint_spacing_m,
        local_reroute_max_waypoints=roads_cfg.local_reroute_max_waypoints,
        local_reroute_corridor_buffer_m=roads_cfg.local_reroute_corridor_buffer_m,
        local_reroute_block_margin_m=roads_cfg.local_reroute_block_margin_m,
        local_reroute_slope_penalty_scale=roads_cfg.local_reroute_slope_penalty_scale,
        local_reroute_river_penalty_scale=roads_cfg.local_reroute_river_penalty_scale,
        local_reroute_collector_snap_bias_m=roads_cfg.local_reroute_collector_snap_bias_m,
        local_reroute_smooth_iters=roads_cfg.local_reroute_smooth_iters,
        local_reroute_simplify_tol_m=roads_cfg.local_reroute_simplify_tol_m,
        local_reroute_max_edges_per_city=roads_cfg.local_reroute_max_edges_per_city,
        local_reroute_apply_to_grid_supplement=roads_cfg.local_reroute_apply_to_grid_supplement,
        local_classic_probe_step_m=roads_cfg.local_classic_probe_step_m,
        local_classic_seed_spacing_m=roads_cfg.local_classic_seed_spacing_m,
        local_classic_max_trace_len_m=roads_cfg.local_classic_max_trace_len_m,
        local_classic_min_trace_len_m=roads_cfg.local_classic_min_trace_len_m,
        local_classic_turn_limit_deg=roads_cfg.local_classic_turn_limit_deg,
        local_classic_branch_prob=roads_cfg.local_classic_branch_prob,
        local_classic_continue_prob=roads_cfg.local_classic_continue_prob,
        local_classic_culdesac_prob=roads_cfg.local_classic_culdesac_prob,
        local_classic_max_segments_per_block=roads_cfg.local_classic_max_segments_per_block,
        local_classic_max_road_distance_m=roads_cfg.local_classic_max_road_distance_m,
        local_classic_depth_decay_power=roads_cfg.local_classic_depth_decay_power,
        local_community_seed_count_per_block=roads_cfg.local_community_seed_count_per_block,
        local_community_spine_prob=roads_cfg.local_community_spine_prob,
        local_arterial_setback_weight=roads_cfg.local_arterial_setback_weight,
        local_collector_follow_weight=roads_cfg.local_collector_follow_weight,
        river_setback_m=roads_cfg.river_setback_m,
        minor_bridge_budget=roads_cfg.minor_bridge_budget,
        max_local_block_area_m2=roads_cfg.max_local_block_area_m2,
        collector_generator=collector_generator_value,
        classic_probe_step_m=classic_probe_step_m,
        classic_seed_spacing_m=classic_seed_spacing_m,
        classic_max_trace_len_m=classic_max_trace_len_m,
        classic_min_trace_len_m=classic_min_trace_len_m,
        classic_turn_limit_deg=classic_turn_limit_deg,
        classic_branch_prob=roads_cfg.classic_branch_prob,
        classic_continue_prob=roads_cfg.classic_continue_prob,
        classic_culdesac_prob=roads_cfg.classic_culdesac_prob,
        classic_max_queue_size=roads_cfg.classic_max_queue_size,
        classic_max_segments=roads_cfg.classic_max_segments,
        classic_max_arterial_distance_m=roads_cfg.classic_max_arterial_distance_m,
        classic_depth_decay_power=roads_cfg.classic_depth_decay_power,
        slope_straight_threshold_deg=roads_cfg.slope_straight_threshold_deg,
        slope_serpentine_threshold_deg=roads_cfg.slope_serpentine_threshold_deg,
        slope_hard_limit_deg=roads_cfg.slope_hard_limit_deg,
        contour_follow_weight=roads_cfg.contour_follow_weight,
        arterial_align_weight=roads_cfg.arterial_align_weight,
        hub_seek_weight=roads_cfg.hub_seek_weight,
        river_snap_dist_m=roads_cfg.river_snap_dist_m,
        river_parallel_bias_weight=roads_cfg.river_parallel_bias_weight,
        river_avoid_weight=roads_cfg.river_avoid_weight,
        tensor_grid_resolution=roads_cfg.tensor_grid_resolution,
        tensor_step_m=roads_cfg.tensor_step_m,
        tensor_seed_spacing_m=roads_cfg.tensor_seed_spacing_m,
        tensor_max_trace_len_m=roads_cfg.tensor_max_trace_len_m,
        tensor_min_trace_len_m=roads_cfg.tensor_min_trace_len_m,
        tensor_turn_limit_deg=roads_cfg.tensor_turn_limit_deg,
        tensor_water_tangent_weight=roads_cfg.tensor_water_tangent_weight,
        tensor_contour_tangent_weight=roads_cfg.tensor_contour_tangent_weight,
        tensor_arterial_align_weight=roads_cfg.tensor_arterial_align_weight,
        tensor_hub_attract_weight=roads_cfg.tensor_hub_attract_weight,
        tensor_water_influence_m=roads_cfg.tensor_water_influence_m,
        tensor_arterial_influence_m=roads_cfg.tensor_arterial_influence_m,
        intersection_snap_radius_m=roads_cfg.intersection_snap_radius_m,
        intersection_t_junction_radius_m=roads_cfg.intersection_t_junction_radius_m,
        intersection_split_tolerance_m=roads_cfg.intersection_split_tolerance_m,
        min_dangle_length_m=roads_cfg.min_dangle_length_m,
        syntax_enable=roads_cfg.syntax_enable,
        syntax_choice_radius_hops=roads_cfg.syntax_choice_radius_hops,
        syntax_prune_low_choice_collectors=roads_cfg.syntax_prune_low_choice_collectors,
        syntax_prune_quantile=roads_cfg.syntax_prune_quantile,
        river_areas=river_areas,
        progress_cb=_progress_subrange(progress_cb, 0.36, 0.78),
        stream_cb=stream_cb,
    )
    _emit_progress(progress_cb, "terrain_visuals", 0.82, "Preparing terrain previews and contours")
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
    _emit_progress(progress_cb, "naming", 0.90, "Assigning place names")

    provider = get_toponymy_provider(config.naming.provider)
    road_edges_for_naming = [
        {"u": e.u, "v": e.v, "road_class": e.road_class, "weight": e.weight}
        for e in road_result.edges
    ]
    names = assign_hub_names(hub_result.hubs, road_edges_for_naming, provider, config.seed)
    name_map = {hub.id: names[i] for i, hub in enumerate(hub_result.hubs)}

    _emit_progress(progress_cb, "core_complete", 1.0, "Core infrastructure generation complete")
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
    if float(metric_values.get("collector_generator_classic_turtle", 0.0)) > 0.5:
        metric_notes.append("Collector generator: classic_turtle")
    elif float(metric_values.get("collector_generator_tensor_streamline", 0.0)) > 0.5:
        metric_notes.append("Collector generator: classic_turtle")
    elif float(metric_values.get("collector_generator_grid_clip", 0.0)) > 0.5:
        metric_notes.append("Collector generator: grid_clip")
    if float(metric_values.get("collector_generator_degraded", 0.0)) > 0.5:
        metric_notes.append("Collector generator degraded to grid_clip")
    trace_count = int(
        metric_values.get(
            "collector_classic_trace_count",
            metric_values.get("collector_tensor_trace_count", 0.0),
        )
    )
    if trace_count > 0:
        metric_notes.append(f"Classic collector traces: {trace_count}")
    riverfront_seed_count = int(metric_values.get("collector_classic_riverfront_seed_count", 0.0))
    riverfront_trace_count = int(metric_values.get("collector_classic_riverfront_trace_count", 0.0))
    if riverfront_seed_count > 0:
        metric_notes.append(f"Classic collector riverfront seeds: {riverfront_seed_count}")
    if riverfront_trace_count > 0:
        metric_notes.append(f"Classic collector riverfront traces: {riverfront_trace_count}")
    arterial_t_attach_count = int(metric_values.get("collector_classic_arterial_t_attach_count", 0.0))
    fallback_attach_count = int(metric_values.get("collector_classic_network_attach_fallback_count", 0.0))
    if arterial_t_attach_count > 0 or fallback_attach_count > 0:
        metric_notes.append(
            f"Classic collector attachments: arterial_t={arterial_t_attach_count}, fallback={fallback_attach_count}"
        )
    local_trace_count = int(metric_values.get("local_classic_trace_count", 0.0))
    if float(metric_values.get("local_generator_classic_sprawl", 0.0)) > 0.5:
        metric_notes.append("Local generator: classic_sprawl")
    elif float(metric_values.get("local_generator_grid_clip", 0.0)) > 0.5:
        metric_notes.append("Local generator: grid_clip")
    if local_trace_count > 0:
        metric_notes.append(f"Classic local fill traces: {local_trace_count}")
        trace_p50 = float(metric_values.get("local_classic_trace_len_p50_m", 0.0))
        trace_p90 = float(metric_values.get("local_classic_trace_len_p90_m", 0.0))
        trace_p99 = float(metric_values.get("local_classic_trace_len_p99_m", 0.0))
        if trace_p50 > 0.0:
            metric_notes.append(
                f"Classic local trace length (m): p50={int(round(trace_p50))}, p90={int(round(trace_p90))}, p99={int(round(trace_p99))}"
            )
            metric_notes.append(
                "Classic local trace target band (500-1000m): "
                f"{float(metric_values.get('local_classic_trace_target_band_rate', 0.0)):.2f} "
                f"(short={float(metric_values.get('local_classic_trace_short_rate', 0.0)):.2f}, "
                f"long={float(metric_values.get('local_classic_trace_long_rate', 0.0)):.2f})"
            )
            nonexc_band = float(metric_values.get("local_classic_trace_nonexception_target_band_rate", 0.0))
            if nonexc_band > 0.0:
                metric_notes.append(f"Classic local trace non-exception target band rate: {nonexc_band:.2f}")
    local_reroute_candidates = int(metric_values.get("local_reroute_candidate_count", 0.0))
    local_reroute_applied = int(metric_values.get("local_reroute_applied_count", 0.0))
    local_reroute_fallback = int(metric_values.get("local_reroute_fallback_count", 0.0))
    if local_reroute_candidates > 0 or local_reroute_applied > 0:
        metric_notes.append(
            f"Classic local geometry reroute: applied {local_reroute_applied}/{max(local_reroute_candidates, 1)} "
            f"(fallback {local_reroute_fallback})"
        )
        metric_notes.append(
            f"Local reroute coverage: {str(getattr(config.roads, 'local_reroute_coverage', 'selective'))}"
        )
    if "local_two_point_edge_ratio" in metric_values:
        metric_notes.append(
            f"Local two-point edge ratio: {float(metric_values.get('local_two_point_edge_ratio', 0.0)):.2f}"
        )
    supplement_budget = int(metric_values.get("local_grid_supplement_budget", 0.0))
    if supplement_budget > 0:
        supplement_added = int(metric_values.get("local_grid_supplement_added_count", 0.0))
        supplement_used_ratio = float(metric_values.get("local_grid_supplement_used_ratio", 0.0))
        metric_notes.append(
            f"Local grid supplement (budgeted): {supplement_added}/{supplement_budget} (ratio={supplement_used_ratio:.2f})"
        )
    local_cul_final = int(metric_values.get("local_culdesac_edge_count_final", 0.0))
    local_cul_pre = int(metric_values.get("local_culdesac_edge_count_pre_topology", 0.0))
    if local_cul_pre > 0 or local_cul_final > 0:
        metric_notes.append(
            f"Local cul-de-sac preservation: {local_cul_final}/{max(local_cul_pre, 1)} "
            f"(ratio={float(metric_values.get('local_culdesac_preserved_ratio', 0.0)):.2f})"
        )
    if float(metric_values.get("syntax_enabled", 0.0)) > 0.5:
        metric_notes.append(f"Space syntax postprocess enabled (pruned={int(metric_values.get('syntax_pruned_count', 0.0))})")

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
        river_mainstem_count=int(sum(1 for r in ctx.river_areas if bool(getattr(r, "is_main_stem", False)))),
        visual_envelope_area_ratio=float(visual_envelope_area_ratio),
        avg_edge_weight=float(metric_values.get("avg_edge_weight", 0.0)),
        road_edge_count_by_class={k: int(v) for k, v in Counter(e.road_class for e in road_edges).items()},
        parcel_count=0,
        median_parcel_area_m2=0.0,
        intersection_snap_to_node_count=int(metric_values.get("intersection_snap_to_node_count", 0.0)),
        intersection_t_junction_count=int(metric_values.get("intersection_t_junction_count", 0.0)),
        intersection_t_split_target_count=int(metric_values.get("intersection_t_split_target_count", 0.0)),
        intersection_crossing_split_count=int(metric_values.get("intersection_crossing_split_count", 0.0)),
        intersection_pruned_dangle_count=int(metric_values.get("intersection_pruned_dangle_count", 0.0)),
        collector_classic_riverfront_seed_count=int(metric_values.get("collector_classic_riverfront_seed_count", 0.0)),
        collector_classic_riverfront_trace_count=int(metric_values.get("collector_classic_riverfront_trace_count", 0.0)),
        collector_classic_arterial_t_attach_count=int(metric_values.get("collector_classic_arterial_t_attach_count", 0.0)),
        collector_classic_network_attach_fallback_count=int(
            metric_values.get("collector_classic_network_attach_fallback_count", 0.0)
        ),
        collector_classic_failed_arterial_attach_count=int(
            metric_values.get("collector_classic_failed_arterial_attach_count", 0.0)
        ),
        local_culdesac_edge_count_pre_topology=int(metric_values.get("local_culdesac_edge_count_pre_topology", 0.0)),
        local_culdesac_edge_count_final=int(metric_values.get("local_culdesac_edge_count_final", 0.0)),
        local_culdesac_preserved_ratio=float(metric_values.get("local_culdesac_preserved_ratio", 0.0)),
        local_reroute_candidate_count=int(metric_values.get("local_reroute_candidate_count", 0.0)),
        local_reroute_applied_count=int(metric_values.get("local_reroute_applied_count", 0.0)),
        local_reroute_fallback_count=int(metric_values.get("local_reroute_fallback_count", 0.0)),
        local_reroute_grid_supplement_applied_count=int(metric_values.get("local_reroute_grid_supplement_applied_count", 0.0)),
        local_two_point_edge_count=int(metric_values.get("local_two_point_edge_count", 0.0)),
        local_two_point_edge_ratio=float(metric_values.get("local_two_point_edge_ratio", 0.0)),
        local_reroute_avg_path_points=float(metric_values.get("local_reroute_avg_path_points", 0.0)),
        local_reroute_avg_length_gain_ratio=float(metric_values.get("local_reroute_avg_length_gain_ratio", 0.0)),
        generation_profile=str(getattr(config.quality, "profile", "balanced")),
        degraded_mode=False,
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
    config: GenerateConfig,
    artifact: CityArtifact,
    extent_m: float,
    resource_sites: Sequence[Any],
    flood_risk_preview: np.ndarray | None,
) -> Tuple[List[PedestrianPath], List[LandBlock], List[ParcelLot]]:
    roads_cfg = config.roads
    collector_spacing_m = float(getattr(roads_cfg, "collector_spacing_m", 420.0) or 420.0)
    max_local_block_area_m2 = float(getattr(roads_cfg, "max_local_block_area_m2", 180000.0) or 180000.0)
    max_block_span_m = max(220.0, min(900.0, 2.0 * collector_spacing_m))
    max_block_area_m2 = min(max_local_block_area_m2, max_block_span_m * max_block_span_m * 0.85)
    extraction = extract_macro_blocks(
        extent_m,
        artifact.roads,
        artifact.river_areas,
        config=BlockExtractionConfig(
            max_block_span_m=max_block_span_m,
            max_block_area_m2=max_block_area_m2,
            max_block_aspect_ratio=8.0,
        ),
    )
    if bool(getattr(config.parcels, "enable", True)):
        parcelization = generate_frontage_parcels(
            extraction.macro_blocks,
            road_network=artifact.roads,
            river_areas=artifact.river_areas,
            pedestrian_width_m=PEDESTRIAN_WIDTH_M,
            config=FrontageParcelConfig(
                residential_target_area_m2=float(getattr(config.parcels, "residential_target_area_m2", 1800.0)),
                mixed_target_area_m2=float(getattr(config.parcels, "mixed_target_area_m2", 2600.0)),
                min_frontage_m=float(getattr(config.parcels, "min_frontage_m", 10.0)),
                min_depth_m=float(getattr(config.parcels, "min_depth_m", 12.0)),
                parcel_local_morphology_coupling=bool(getattr(config.parcels, "parcel_local_morphology_coupling", True)),
                parcel_culdesac_frontage_relaxation=float(getattr(config.parcels, "parcel_culdesac_frontage_relaxation", 0.18)),
                parcel_local_depth_bias=float(getattr(config.parcels, "parcel_local_depth_bias", 0.10)),
                parcel_curvilinear_split_bias=float(getattr(config.parcels, "parcel_curvilinear_split_bias", 0.20)),
                seed=int(config.seed),
            ),
        )
    else:
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
    notes = list(getattr(artifact.metrics, "notes", []) or [])
    notes.extend(
        [
            "block_extractor:topology_fallback",
            f"block_topology_count:{int(getattr(extraction, 'topology_block_count', 0))}",
            f"block_fallback_count:{int(getattr(extraction, 'fallback_block_count', 0))}",
            f"block_max_span_cap_m:{int(round(max_block_span_m))}",
        ]
    )
    artifact.metrics.notes = notes
    return artifact.pedestrian_paths, artifact.blocks, artifact.parcels


def _refresh_final_metrics(
    artifact: CityArtifact,
    config: GenerateConfig,
    *,
    degraded_mode: bool = False,
) -> None:
    edge_counts = Counter(str(e.road_class) for e in artifact.roads.edges)
    river_mainstem_count = int(sum(1 for r in (artifact.river_areas or []) if bool(getattr(r, "is_main_stem", False))))
    parcel_areas = [float(p.area_m2) for p in (artifact.parcels or []) if float(getattr(p, "area_m2", 0.0)) > 0.0]
    parcel_median = float(np.median(np.asarray(parcel_areas, dtype=np.float64))) if parcel_areas else 0.0

    artifact.metrics.road_edge_count_by_class = {k: int(v) for k, v in edge_counts.items()}
    artifact.metrics.parcel_count = int(len(artifact.parcels or []))
    artifact.metrics.median_parcel_area_m2 = parcel_median
    artifact.metrics.river_mainstem_count = river_mainstem_count
    artifact.metrics.generation_profile = str(getattr(config.quality, "profile", "balanced"))
    artifact.metrics.degraded_mode = bool(degraded_mode)


def _build_analysis_and_land_layers(
    config: GenerateConfig,
    ctx: _CoreGenerationContext,
    artifact: CityArtifact,
    *,
    progress_cb: ProgressCallback | None = None,
) -> _AnalysisAndLandLayers:
    _emit_progress(progress_cb, "analysis", 0.08, "Computing suitability and flood analysis")
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

    traffic_result = assign_edge_flows(artifact.hubs, artifact.roads)
    _emit_progress(progress_cb, "buildings", 0.42, "Generating buildings and green zones previews")
    building_footprints = generate_building_footprints(
        seed=config.seed,
        extent_m=config.extent_m,
        hubs=artifact.hubs,
        population_potential_preview=population_potential,
        flood_risk_preview=analysis.flood_risk,
    )
    green_zones_preview = generate_green_zones_preview(
        suitability_preview=analysis.suitability,
        flood_risk_preview=analysis.flood_risk,
        population_potential_preview=population_potential,
    )

    pedestrian_paths, land_blocks, parcel_lots = _attach_land_use_layers(
        config,
        artifact,
        extent_m=config.extent_m,
        resource_sites=resource_sites,
        flood_risk_preview=analysis.flood_risk,
    )
    _emit_progress(progress_cb, "parcels", 0.90, "Classifying blocks and parcels")
    _refresh_final_metrics(artifact, config)
    _emit_progress(progress_cb, "analysis_complete", 1.0, "Analysis and land layers complete")

    return _AnalysisAndLandLayers(
        analysis=analysis,
        resource_sites=list(resource_sites),
        population_potential=population_potential,
        traffic_result=traffic_result,
        building_footprints=list(building_footprints),
        green_zones_preview=green_zones_preview,
        pedestrian_paths=list(pedestrian_paths),
        land_blocks=list(land_blocks),
        parcel_lots=list(parcel_lots),
    )


def generate_city(config: GenerateConfig, *, progress_cb: ProgressCallback | None = None) -> CityArtifact:
    t0 = perf_counter()
    _emit_progress(progress_cb, "start", 0.0, "Starting city generation")
    ctx = _generate_core_context(config, progress_cb=_progress_subrange(progress_cb, 0.02, 0.62))
    _emit_progress(progress_cb, "artifact", 0.68, "Building city artifact payload")
    artifact = _build_city_artifact_from_core(config, ctx, duration_ms=0.0)
    _build_analysis_and_land_layers(config, ctx, artifact, progress_cb=_progress_subrange(progress_cb, 0.70, 0.96))
    duration_ms = (perf_counter() - t0) * 1000.0
    artifact.meta.duration_ms = float(duration_ms)
    artifact.meta.generated_at_utc = datetime.now(timezone.utc).isoformat()
    _emit_progress(progress_cb, "done", 1.0, "City generation complete")
    return artifact


def generate_city_staged(
    config: GenerateConfig,
    *,
    progress_cb: ProgressCallback | None = None,
    stream_cb: StreamCallback | None = None,
) -> StagedCityResponse:
    t0 = perf_counter()
    _emit_progress(progress_cb, "start", 0.0, "Starting staged city generation")
    ctx = _generate_core_context(
        config,
        progress_cb=_progress_subrange(progress_cb, 0.02, 0.62),
        stream_cb=stream_cb,
    )
    _emit_progress(progress_cb, "artifact", 0.68, "Building base artifact")
    final_artifact = _build_city_artifact_from_core(config, ctx, duration_ms=0.0)
    layers = _build_analysis_and_land_layers(config, ctx, final_artifact, progress_cb=_progress_subrange(progress_cb, 0.70, 0.93))

    duration_ms = (perf_counter() - t0) * 1000.0
    final_artifact.meta.duration_ms = float(duration_ms)
    final_artifact.meta.generated_at_utc = datetime.now(timezone.utc).isoformat()

    _emit_progress(progress_cb, "stages", 0.96, "Building timeline stages")
    stages = build_stages(
        final_artifact=final_artifact,
        suitability_preview=layers.analysis.suitability,
        flood_risk_preview=layers.analysis.flood_risk,
        population_potential_preview=layers.population_potential,
        resource_sites=layers.resource_sites,
        traffic_edge_flows=layers.traffic_result.edge_flows,
        building_footprints=layers.building_footprints,
        green_zones_preview=layers.green_zones_preview,
        terrain_class_preview=ctx.terrain_visuals.terrain_class_preview,
        hillshade_preview=ctx.terrain_visuals.hillshade_preview,
        contour_lines=ctx.contour_lines,
        river_area_polygons=final_artifact.river_areas,
        pedestrian_paths=layers.pedestrian_paths,
        land_blocks=layers.land_blocks,
        parcel_lots=layers.parcel_lots,
    )
    _emit_progress(progress_cb, "done", 1.0, "Staged city generation complete")
    return StagedCityResponse(final_artifact=final_artifact, stages=stages)


def generate_city_json(config: GenerateConfig) -> str:
    return artifact_to_json(generate_city(config))
