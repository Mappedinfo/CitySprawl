from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from collections import defaultdict
from typing import Any, Callable, Dict, Optional, Sequence

import numpy as np

from engine.core.geometry import Segment, Vec2, project_point_to_segment, segment_intersection
from engine.roads.classic_growth import (
    _clamp_turn,
    _emit_stream_event,
    _flatten_segments_from_edges,
    _iter_polyline_points,
    _nearest_hub_vector,
    _nearest_road_distance_and_projection,
    _nearest_segment_tangent,
    _polyline_length,
    _polyline_segments,
    _turn_vec,
)
from engine.roads.terrain_probe import TerrainProbe, TerrainProbeConfig


@dataclass
class LocalClassicFillConfig:
    local_spacing_m: float = 130.0
    # Local portal seeds are precomputed on major roads (arterial+collector)
    # so local growth visually and structurally branches from the major network.
    local_major_seed_spacing_min_m: float = 400.0
    local_major_seed_spacing_max_m: float = 500.0
    local_major_seed_inset_m: float = 10.0
    # Semantic local trace targets (trace continuity, not final topology-split edges).
    # Current design favors long mainlines while downstream topology may still split edges.
    local_trace_target_min_m: float = 1200.0
    local_trace_target_max_m: float = 4800.0
    local_trace_soft_cap_m: float = 5600.0
    local_trace_force_continue_until_min: bool = True
    local_trace_exception_small_block_long_axis_m: float = 320.0
    local_classic_probe_step_m: float = 18.0
    local_classic_seed_spacing_m: float = 110.0
    local_classic_max_trace_len_m: float = 6000.0
    # Preferred user-facing name: Minor Local Run hard cap. Keep legacy trace cap for compatibility.
    local_minor_run_hard_cap_m: float = 6000.0
    local_classic_min_trace_len_m: float = 48.0
    local_classic_turn_limit_deg: float = 54.0
    local_classic_branch_prob: float = 0.62
    local_classic_continue_prob: float = 0.70
    local_classic_culdesac_prob: float = 0.42
    local_classic_max_segments_per_block: int = 28
    local_classic_max_road_distance_m: float = 500.0
    local_classic_depth_decay_power: float = 1.5
    local_community_seed_count_per_block: int = 3
    local_community_spine_prob: float = 0.28
    local_arterial_setback_weight: float = 0.5
    local_collector_follow_weight: float = 0.9
    # Root mainline sub-local connector spawning cadence and behavior.
    local_sub_branch_interval_min_m: float = 200.0
    local_sub_branch_interval_max_m: float = 400.0
    local_sub_branch_connector_seek_radius_m: float = 1200.0
    local_sub_branch_max_depth: int = 2
    local_sub_branch_length_cap_m: float = 1800.0
    local_allow_disconnected_accept: bool = False
    slope_straight_threshold_deg: float = 5.0
    slope_serpentine_threshold_deg: float = 15.0
    slope_hard_limit_deg: float = 22.0
    contour_follow_weight: float = 0.9
    river_snap_dist_m: float = 28.0
    river_parallel_bias_weight: float = 1.0
    river_avoid_weight: float = 1.2
    river_setback_m: float = 18.0


@dataclass(order=True)
class _State:
    priority: float
    pos: Vec2
    direction: Vec2
    block_idx: int
    depth: int = 0
    lineage_id: str = field(default="", compare=False)
    parent_lineage_id: Optional[str] = field(default=None, compare=False)
    from_major_portal: bool = field(default=False, compare=False)
    branch_role: str = field(default="mainline", compare=False)
    next_sub_branch_trigger_m: float = field(default=float("inf"), compare=False)
    local_touch_count: int = field(default=0, compare=False)
    reached_trace_cap: bool = field(default=False, compare=False)
    terminal_stop_reason: str = field(default="", compare=False)


@dataclass
class LocalTraceMeta:
    block_idx: int
    is_spine_candidate: bool = False
    connected_to_collector: bool = False
    culdesac: bool = False
    depth: int = 0
    trace_lineage_id: Optional[str] = None
    parent_trace_lineage_id: Optional[str] = None
    minor_local_continuity_id: Optional[str] = None
    parent_minor_local_continuity_id: Optional[str] = None
    seed_origin: str = "major_portal_seed"
    branch_role: str = "mainline"
    trace_len_m: float = 0.0
    local_touch_count: int = 0
    reached_trace_cap: bool = False
    terminal_stop_reason: str = ""
    is_overlimit_unconnected_candidate: bool = False


def _point_in_poly_or_close(poly, p: Vec2, tol: float = 1.0) -> bool:
    try:
        from shapely.geometry import Point  # type: ignore
    except Exception:
        # Without shapely, trust bounds as weak fallback.
        minx, miny, maxx, maxy = poly.bounds
        return (minx - tol) <= p.x <= (maxx + tol) and (miny - tol) <= p.y <= (maxy + tol)
    pt = Point(float(p.x), float(p.y))
    try:
        if bool(poly.buffer(float(tol)).contains(pt)):
            return True
    except Exception:
        pass
    try:
        return bool(poly.contains(pt))
    except Exception:
        return False


def _find_block_index_for_point(blocks: Sequence[object], p: Vec2, *, preferred_idx: int = -1, tol: float = 1.0) -> Optional[int]:
    if 0 <= int(preferred_idx) < len(blocks):
        if _point_in_poly_or_close(blocks[int(preferred_idx)], p, tol=tol):
            return int(preferred_idx)
    for i, block in enumerate(blocks):
        if i == int(preferred_idx):
            continue
        if _point_in_poly_or_close(block, p, tol=tol):
            return int(i)
    return None


def _classify_network_contact_mode(
    *,
    approach_dir: Vec2,
    contact_point: Vec2,
    candidate_segments: Sequence[Segment],
) -> str:
    if not candidate_segments:
        return "unknown"
    tan, _ = _nearest_segment_tangent(contact_point, candidate_segments)
    if tan is None or tan.length() <= 1e-9:
        return "unknown"
    a = approach_dir.normalized()
    t = tan.normalized()
    if a.length() <= 1e-9 or t.length() <= 1e-9:
        return "unknown"
    dot = float(max(-1.0, min(1.0, a.dot(t))))
    # Opposing: roughly head-on; stop and merge.
    if dot <= -0.82:
        return "opposing"
    # Perpendicular-ish: allow T/cross while trunk continues.
    if abs(dot) <= 0.34:
        return "perpendicular"
    # Near-parallel: treat as merge to avoid duplicate overlapping traces.
    if abs(dot) >= 0.82:
        return "parallel"
    return "oblique"


def _major_repulsion_vector(
    p: Vec2,
    major_segments: Sequence[Segment],
    *,
    influence_radius_m: float,
    max_samples: int = 12,
) -> Optional[Vec2]:
    if not major_segments or influence_radius_m <= 1e-6:
        return None
    samples: list[tuple[float, Vec2]] = []
    for seg in major_segments:
        proj = project_point_to_segment(p, seg)
        d = p.distance_to(proj)
        if d > influence_radius_m:
            continue
        away = (p - proj).normalized()
        if away.length() <= 1e-9:
            continue
        samples.append((float(d), away))
    if not samples:
        return None
    samples.sort(key=lambda item: item[0])
    acc = Vec2(0.0, 0.0)
    for d, away in samples[: max(1, int(max_samples))]:
        w = (1.0 - min(1.0, d / max(influence_radius_m, 1e-6))) ** 2
        acc = Vec2(acc.x + away.x * w, acc.y + away.y * w)
    out = acc.normalized()
    return out if out.length() > 1e-9 else None


def _major_clearance_score(
    p: Vec2,
    major_segments: Sequence[Segment],
    *,
    influence_radius_m: float,
    k: int = 6,
) -> float:
    if not major_segments or influence_radius_m <= 1e-6:
        return float(influence_radius_m if influence_radius_m > 0.0 else 0.0)
    dists: list[float] = []
    for seg in major_segments:
        proj = project_point_to_segment(p, seg)
        d = p.distance_to(proj)
        dists.append(float(min(max(d, 0.0), influence_radius_m)))
    if not dists:
        return float(influence_radius_m)
    dists.sort()
    use = dists[: max(1, int(k))]
    # Weighted toward the closest few segments to reflect "major-road corridor" pressure.
    weighted = 0.0
    total_w = 0.0
    for i, d in enumerate(use):
        w = 1.0 / float(i + 1)
        weighted += d * w
        total_w += w
    return float(weighted / total_w) if total_w > 0.0 else float(use[0])


def _block_centroid_vecs(block, seed_count: int, rng: np.random.Generator) -> list[Vec2]:
    out: list[Vec2] = []
    c = getattr(block, "centroid", None)
    if c is not None:
        out.append(Vec2(float(c.x), float(c.y)))
    rep = getattr(block, "representative_point", None)
    if callable(rep):
        p = rep()
        out.append(Vec2(float(p.x), float(p.y)))
    if seed_count <= 2:
        return out
    try:
        mrr = block.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)
    except Exception:
        return out
    if len(coords) >= 4:
        for i in range(min(seed_count - 2, 4)):
            x0, y0 = coords[i]
            x1, y1 = coords[i + 1]
            t = float(rng.uniform(0.25, 0.75))
            out.append(Vec2(float(x0 + (x1 - x0) * t), float(y0 + (y1 - y0) * t)))
    return out


def _major_axis_angle_deg(block) -> float:
    try:
        mrr = block.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)
    except Exception:
        return 0.0
    if len(coords) < 4:
        return 0.0
    best_len = -1.0
    best_ang = 0.0
    for i in range(4):
        x0, y0 = coords[i]
        x1, y1 = coords[i + 1]
        dx = float(x1 - x0)
        dy = float(y1 - y0)
        l2 = dx * dx + dy * dy
        if l2 > best_len:
            best_len = l2
            best_ang = float(np.degrees(np.arctan2(dy, dx)))
    return best_ang


def _block_dims(block) -> tuple[float, float]:
    try:
        mrr = block.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)
        if len(coords) >= 4:
            lens = []
            for i in range(4):
                x0, y0 = coords[i]
                x1, y1 = coords[i + 1]
                dx = float(x1 - x0)
                dy = float(y1 - y0)
                lens.append((dx * dx + dy * dy) ** 0.5)
            lens = [float(v) for v in lens if v > 1e-6]
            if lens:
                return (min(lens), max(lens))
    except Exception:
        pass
    minx, miny, maxx, maxy = block.bounds
    w = float(maxx - minx)
    h = float(maxy - miny)
    return (min(w, h), max(w, h))


def _unit_from_angle_deg(a: float) -> Vec2:
    r = np.deg2rad(float(a))
    return Vec2(float(np.cos(r)), float(np.sin(r))).normalized()


def _quantile(vals: Sequence[float], q: float) -> float:
    if not vals:
        return 0.0
    arr = sorted(float(v) for v in vals)
    idx = int(round((len(arr) - 1) * float(q)))
    idx = max(0, min(len(arr) - 1, idx))
    return float(arr[idx])


def _sample_polyline_point_and_tangent(points: Sequence[Vec2], dist_m: float) -> tuple[Optional[Vec2], Optional[Vec2]]:
    if len(points) < 2:
        return None, None
    total = 0.0
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        seg = Segment(a, b)
        seg_len = seg.length()
        if seg_len <= 1e-6:
            continue
        nxt_total = total + seg_len
        if dist_m <= nxt_total + 1e-9:
            t = max(0.0, min(1.0, (float(dist_m) - total) / max(seg_len, 1e-9)))
            p = seg.point_at(t)
            tan = seg.vector().normalized()
            return p, tan
        total = nxt_total
    last_seg = Segment(points[-2], points[-1])
    return points[-1], last_seg.vector().normalized()


def _estimate_block_forward_clearance(
    block: object,
    start: Vec2,
    direction: Vec2,
    *,
    max_dist_m: float,
    step_m: float = 24.0,
) -> float:
    d = 0.0
    u = direction.normalized()
    if u.length() <= 1e-9:
        return 0.0
    step = max(6.0, float(step_m))
    max_dist = max(0.0, float(max_dist_m))
    while d + step <= max_dist:
        cand = Vec2(start.x + u.x * (d + step), start.y + u.y * (d + step))
        if not _point_in_poly_or_close(block, cand, tol=1.0):
            break
        d += step
    return float(d)


def _nearest_point_on_segments(p: Vec2, segments: Sequence[Segment]) -> tuple[float, Optional[Vec2]]:
    best_d = float("inf")
    best_p: Optional[Vec2] = None
    for seg in segments:
        if seg.length() <= 1e-9:
            continue
        proj = project_point_to_segment(p, seg)
        d = p.distance_to(proj)
        if d < best_d:
            best_d = d
            best_p = proj
    return float(best_d), best_p


def _nearest_endpoint_target(
    p: Vec2,
    endpoints: Sequence[tuple[Vec2, Optional[str]]],
    *,
    exclude_lineage: Optional[str],
    max_dist_m: float,
) -> tuple[float, Optional[Vec2], Optional[str]]:
    best_d = float("inf")
    best_p: Optional[Vec2] = None
    best_lineage: Optional[str] = None
    max_d = float(max_dist_m)
    for ep, lineage_id in endpoints:
        if exclude_lineage and lineage_id and str(lineage_id) == str(exclude_lineage):
            continue
        d = p.distance_to(ep)
        if d > max_d:
            continue
        if d < best_d:
            best_d = d
            best_p = ep
            best_lineage = lineage_id
    return float(best_d), best_p, best_lineage


def generate_classic_local_fill(
    *,
    extent_m: float,
    height: Optional[np.ndarray],
    slope: np.ndarray,
    river_mask: np.ndarray,
    river_areas: Optional[Sequence[object]],
    river_union: object,
    nodes: Sequence[object],
    edges: Sequence[object],
    hubs: Sequence[object],
    blocks: Sequence[object],
    cfg: LocalClassicFillConfig,
    seed: int,
    stream_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> tuple[list[list[Vec2]], list[bool], list[LocalTraceMeta], list[str], dict[str, float]]:
    rng = np.random.default_rng(int(seed) + 9203)
    seed_rng = np.random.default_rng(int(seed) + 9217)
    probe = TerrainProbe(
        extent_m=float(extent_m),
        height=height,
        slope=slope,
        river_mask=river_mask,
        river_areas=river_areas,
        river_union=river_union,
        cfg=TerrainProbeConfig(
            slope_straight_threshold_deg=float(cfg.slope_straight_threshold_deg),
            slope_serpentine_threshold_deg=float(cfg.slope_serpentine_threshold_deg),
            slope_hard_limit_deg=float(cfg.slope_hard_limit_deg),
            contour_follow_weight=float(cfg.contour_follow_weight),
            river_snap_dist_m=float(cfg.river_snap_dist_m),
            river_parallel_bias_weight=float(cfg.river_parallel_bias_weight),
            river_avoid_weight=float(cfg.river_avoid_weight),
            river_setback_m=float(cfg.river_setback_m),
        ),
    )
    base_segments = _flatten_segments_from_edges(edges, nodes, road_classes={"arterial", "collector", "local"})
    collector_segments = _flatten_segments_from_edges(edges, nodes, road_classes={"collector"})
    arterial_segments = _flatten_segments_from_edges(edges, nodes, road_classes={"arterial"})
    existing_local_segments = _flatten_segments_from_edges(edges, nodes, road_classes={"local"})
    higher_order_segments = list(arterial_segments) + list(collector_segments)
    runtime_segments: list[Segment] = []

    queue: list[_State] = []
    block_seed_counts: list[int] = []
    block_trace_len_caps: list[float] = []
    block_endpoint_span_caps: list[float] = []
    block_long_axes: list[float] = []
    block_trace_target_enabled: list[bool] = []
    local_spacing_m = max(24.0, float(getattr(cfg, "local_spacing_m", 130.0) or 130.0))
    trace_target_min_m = max(120.0, float(getattr(cfg, "local_trace_target_min_m", 1200.0) or 1200.0))
    trace_target_max_m = max(trace_target_min_m + 80.0, float(getattr(cfg, "local_trace_target_max_m", 4800.0) or 4800.0))
    trace_soft_cap_m = max(trace_target_max_m + 120.0, float(getattr(cfg, "local_trace_soft_cap_m", 5600.0) or 5600.0))
    small_block_exception_long_axis_m = max(
        260.0,
        float(getattr(cfg, "local_trace_exception_small_block_long_axis_m", 650.0) or 650.0),
    )
    force_continue_until_min = bool(getattr(cfg, "local_trace_force_continue_until_min", True))
    trace_cap_sum = 0.0
    trace_cap_n = 0
    major_seed_spacing_min_m = max(180.0, float(getattr(cfg, "local_major_seed_spacing_min_m", 400.0) or 400.0))
    major_seed_spacing_max_m = max(major_seed_spacing_min_m, float(getattr(cfg, "local_major_seed_spacing_max_m", 500.0) or 500.0))
    major_seed_inset_m = max(4.0, float(getattr(cfg, "local_major_seed_inset_m", 10.0) or 10.0))
    major_seed_portal_by_block: list[list[tuple[Vec2, Vec2, float]]] = [[] for _ in blocks]
    major_seed_source_count = 0
    fallback_centroid_seed_count = 0
    portal_interval_samples_m: list[float] = []
    node_lookup = {
        str(getattr(n, "id")): Vec2(float(getattr(n, "pos").x), float(getattr(n, "pos").y))
        for n in nodes
        if hasattr(n, "pos")
    }
    existing_local_endpoints: list[tuple[Vec2, Optional[str]]] = []
    for edge in edges:
        if str(getattr(edge, "road_class", "")).lower() != "local":
            continue
        edge_pts = _iter_polyline_points(edge, node_lookup)
        if len(edge_pts) >= 2:
            existing_local_endpoints.append((edge_pts[0], None))
            existing_local_endpoints.append((edge_pts[-1], None))

    def _add_major_portal_seed(block_idx: int, pos: Vec2, inward_dir: Vec2, clearance_m: float) -> bool:
        if not (0 <= int(block_idx) < len(major_seed_portal_by_block)):
            return False
        d0 = inward_dir.normalized()
        if d0.length() <= 1e-9:
            return False
        if probe.check_water_hit(pos):
            return False
        bucket = major_seed_portal_by_block[int(block_idx)]
        dedupe_dist = min(260.0, max(120.0, major_seed_spacing_min_m * 0.48))
        for ep, edir, _eclear in bucket:
            if pos.distance_to(ep) < dedupe_dist and abs(d0.dot(edir.normalized())) > 0.6:
                return False
        bucket.append((pos, d0, float(max(0.0, clearance_m))))
        return True

    # Precompute sub-branch trigger cadence for root seed initialization.
    _sub_branch_interval_min_seed_m = max(
        max(8.0, float(cfg.local_classic_probe_step_m)) * 4.0,
        float(getattr(cfg, "local_sub_branch_interval_min_m", 200.0) or 200.0),
    )
    _sub_branch_interval_max_seed_m = max(
        _sub_branch_interval_min_seed_m,
        float(getattr(cfg, "local_sub_branch_interval_max_m", 400.0) or 400.0),
    )

    def _sample_initial_sub_branch_trigger_distance() -> float:
        return float(rng.uniform(_sub_branch_interval_min_seed_m, _sub_branch_interval_max_seed_m))

    # Precompute local seed anchors on major roads (arterial + collector) so
    # local roads branch from the major network instead of spawning from block edges.
    for edge in edges:
        rc = str(getattr(edge, "road_class", "")).lower()
        if rc not in {"arterial", "collector"}:
            continue
        pts = _iter_polyline_points(edge, node_lookup)
        if len(pts) < 2:
            continue
        total_len = _polyline_length(pts)
        if total_len < max(major_seed_spacing_min_m * 0.35, 160.0):
            continue

        sample_dists: list[float] = []
        if total_len <= major_seed_spacing_max_m * 1.35:
            if total_len >= max(major_seed_spacing_min_m * 0.45, 180.0):
                sample_dists = [0.5 * total_len]
        else:
            first = float(seed_rng.uniform(major_seed_spacing_min_m * 0.55, major_seed_spacing_max_m * 0.95))
            d = max(0.0, min(total_len - 1e-6, first))
            while d < total_len:
                sample_dists.append(float(d))
                interval = float(seed_rng.uniform(major_seed_spacing_min_m, major_seed_spacing_max_m))
                if interval > 1e-6:
                    portal_interval_samples_m.append(interval)
                d += interval

        for dist_m in sample_dists:
            p, tan = _sample_polyline_point_and_tangent(pts, float(dist_m))
            if p is None or tan is None or tan.length() <= 1e-9:
                continue
            left = Vec2(-tan.y, tan.x).normalized()
            right = Vec2(tan.y, -tan.x).normalized()
            side_candidates: list[tuple[int, Vec2, Vec2, float]] = []
            clearance_probe_cap = max(420.0, min(float(extent_m) * 0.35, 1400.0))
            for nrm in (left, right):
                probe_pt = Vec2(p.x + nrm.x * major_seed_inset_m, p.y + nrm.y * major_seed_inset_m)
                bi = _find_block_index_for_point(blocks, probe_pt, preferred_idx=-1, tol=max(1.0, major_seed_inset_m * 0.55))
                if bi is None:
                    continue
                clearance = _estimate_block_forward_clearance(
                    blocks[int(bi)],
                    probe_pt,
                    nrm,
                    max_dist_m=clearance_probe_cap,
                    step_m=max(float(cfg.local_classic_probe_step_m), 18.0),
                )
                side_candidates.append((int(bi), probe_pt, nrm, float(clearance)))
            if not side_candidates:
                continue
            min_clearance_keep = max(160.0, local_spacing_m * 1.8)
            best_clearance = max(float(c[3]) for c in side_candidates)
            for bi, probe_pt, nrm, clearance in side_candidates:
                if len(side_candidates) >= 2 and clearance < min_clearance_keep and best_clearance >= min_clearance_keep:
                    continue
                if clearance < max(40.0, local_spacing_m * 0.6):
                    continue
                if _add_major_portal_seed(int(bi), probe_pt, nrm, clearance):
                    major_seed_source_count += 1

    for bi, block in enumerate(blocks):
        area = float(getattr(block, "area", 0.0) or 0.0)
        bmin, bmax = _block_dims(block)
        block_long_axes.append(float(bmax))
        target_enabled = bool(bmax >= small_block_exception_long_axis_m)
        block_trace_target_enabled.append(target_enabled)
        legacy_max_cap = max(float(cfg.local_classic_min_trace_len_m) + 8.0, float(cfg.local_classic_max_trace_len_m))

        if target_enabled:
            dynamic_trace_cap = min(
                max(trace_target_max_m, trace_soft_cap_m),
                max(trace_target_max_m, min(trace_soft_cap_m, max(local_spacing_m * 4.0, bmax * 1.15))),
            )
            # Don't let the legacy 420m default artificially prevent 500m-1km traces.
            if legacy_max_cap >= trace_target_max_m * 0.95:
                dynamic_trace_cap = min(dynamic_trace_cap, max(trace_target_max_m, legacy_max_cap))
            dynamic_span_cap = min(
                dynamic_trace_cap * 0.98,
                max(trace_target_min_m * 0.80, min(bmax * 0.96, trace_target_max_m * 0.95)),
            )
            if bmin > 1e-6:
                dynamic_span_cap = min(dynamic_span_cap, max(trace_target_min_m * 0.75, bmin * 3.4))
        else:
            dynamic_trace_cap = min(
                max(trace_soft_cap_m, legacy_max_cap),
                max(
                    legacy_max_cap,
                    max(float(cfg.local_classic_min_trace_len_m) * 1.4, min(max(local_spacing_m * 3.4, 180.0), max(local_spacing_m * 2.3, bmax * 0.95))),
                ),
            )
            dynamic_span_cap = min(
                dynamic_trace_cap * 0.95,
                max(local_spacing_m * 1.55, min(bmax * 0.85, local_spacing_m * 3.1)),
            )
            if bmin > 1e-6:
                dynamic_span_cap = min(dynamic_span_cap, max(local_spacing_m * 1.35, bmin * 2.8))
        dynamic_span_cap = max(32.0, float(dynamic_span_cap))
        dynamic_trace_cap = max(float(cfg.local_classic_min_trace_len_m) * 1.2, float(dynamic_trace_cap))
        block_trace_len_caps.append(float(dynamic_trace_cap))
        block_endpoint_span_caps.append(float(dynamic_span_cap))
        if area >= 2500.0:
            trace_cap_sum += float(dynamic_trace_cap)
            trace_cap_n += 1
        if area < 2500.0:
            block_seed_counts.append(0)
            continue
        seeds: list[tuple[Vec2, Vec2, bool]] = []
        if bi < len(major_seed_portal_by_block):
            for sp, inward, _clearance in sorted(major_seed_portal_by_block[bi], key=lambda item: -float(item[2])):
                seeds.append((sp, inward, True))
        if not seeds:
            # Fallback for blocks with no major-road contact (e.g. residual supplement polygons):
            # use centroid-like seeds, not boundary seeds, to preserve "grow from network inward"
            # visuals in the primary local stage and avoid edge-to-center artifacts.
            fallback = _block_centroid_vecs(block, int(max(1, cfg.local_community_seed_count_per_block)), rng)
            major = _unit_from_angle_deg(_major_axis_angle_deg(block))
            if major.length() <= 1e-9:
                major = Vec2(1.0, 0.0)
            for sp in fallback:
                if not _point_in_poly_or_close(block, sp, tol=2.0):
                    continue
                seeds.append((sp, major, False))
                fallback_centroid_seed_count += 1

        added = 0
        for sp, inward_dir, from_major_portal in seeds:
            if not _point_in_poly_or_close(block, sp, tol=2.0):
                continue
            if probe.check_water_hit(sp):
                continue
            # Use the inward perpendicular direction directly
            d0 = inward_dir.normalized()
            if d0.length() <= 1e-9:
                continue
            lineage_id = f"b{int(bi)}.r{int(added)}"
            heapq.heappush(
                queue,
                _State(
                    priority=float(rng.uniform(0.0, 0.8)),
                    pos=sp,
                    direction=d0,
                    block_idx=bi,
                    depth=0,
                    lineage_id=lineage_id,
                        parent_lineage_id=None,
                        from_major_portal=bool(from_major_portal),
                        branch_role="mainline",
                        next_sub_branch_trigger_m=_sample_initial_sub_branch_trigger_distance(),
                    ),
                )
            added += 1
        block_seed_counts.append(added)

    traces: list[list[Vec2]] = []
    cul_flags: list[bool] = []
    trace_meta: list[LocalTraceMeta] = []
    per_block_counts = defaultdict(int)
    notes = [f"local_classic_seed_states:{len(queue)}"]
    stop_reasons: dict[str, int] = {}
    branch_enq = 0
    cul_count = 0
    accepted_trace_lengths: list[float] = []
    accepted_trace_stop_reasons: list[str] = []
    accepted_trace_block_indices: list[int] = []
    accepted_trace_cul_flags: list[bool] = []
    trace_stream_attempt_seq = 0
    contact_mode_counts: dict[str, int] = {
        "opposing": 0,
        "parallel": 0,
        "perpendicular_continue": 0,
        "oblique_continue": 0,
    }
    runtime_local_endpoints: list[tuple[Vec2, Optional[str]]] = []
    major_repel_eval_count = 0
    major_repel_apply_count = 0
    major_repel_post_contact_boost_count = 0
    major_repel_no_valid_candidate_count = 0
    major_repel_clearance_gain_sum_m = 0.0
    local_touch_count_total = 0
    local_trace_reached_cap_count = 0
    local_trace_overlimit_unconnected_count = 0
    local_trace_over_6km_count = 0
    local_sub_branch_trigger_count = 0
    local_sub_branch_left_spawn_count = 0
    local_sub_branch_right_spawn_count = 0
    local_sub_branch_connector_touch_count = 0

    step_m = max(8.0, float(cfg.local_classic_probe_step_m))
    junction_probe = max(8.0, step_m * 0.85)
    sub_branch_interval_min_m = max(step_m * 4.0, float(getattr(cfg, "local_sub_branch_interval_min_m", 200.0) or 200.0))
    sub_branch_interval_max_m = max(
        sub_branch_interval_min_m,
        float(getattr(cfg, "local_sub_branch_interval_max_m", 400.0) or 400.0),
    )
    sub_branch_seek_radius_m = max(
        120.0,
        float(getattr(cfg, "local_sub_branch_connector_seek_radius_m", 1200.0) or 1200.0),
    )
    sub_branch_max_depth = max(0, int(getattr(cfg, "local_sub_branch_max_depth", 2) or 2))
    sub_branch_length_cap_m = max(
        float(cfg.local_classic_min_trace_len_m) * 2.0,
        float(getattr(cfg, "local_sub_branch_length_cap_m", 1800.0) or 1800.0),
    )
    local_trace_hard_cap_m = max(
        float(cfg.local_classic_min_trace_len_m) + 1.0,
        float(getattr(cfg, "local_minor_run_hard_cap_m", cfg.local_classic_max_trace_len_m) or cfg.local_classic_max_trace_len_m),
    )
    major_segments = higher_order_segments
    major_repel_influence_radius_m = max(local_spacing_m * 1.15, 140.0)
    major_repel_max_samples = 12
    major_clearance_k = 6
    detach_influence_radius_m = max(local_spacing_m * 1.2, 150.0)
    detach_target_clearance_m = max(local_spacing_m * 0.9, 95.0)
    detach_max_len_m = max(local_spacing_m * 2.8, 280.0)
    post_contact_detach_boost_steps = 6
    detach_collector_follow_cap = 0.32
    lambda_clearance = 1.15
    clearance_gain_min_m = 4.0

    def _sample_sub_branch_trigger_distance() -> float:
        return float(rng.uniform(sub_branch_interval_min_m, sub_branch_interval_max_m))

    while queue:
        st = heapq.heappop(queue)
        if st.block_idx >= len(blocks):
            continue
        if per_block_counts[st.block_idx] >= int(cfg.local_classic_max_segments_per_block):
            continue
        block = blocks[st.block_idx]
        if not _point_in_poly_or_close(block, st.pos, tol=2.0):
            continue
        if probe.check_water_hit(st.pos):
            continue
        if runtime_segments:
            d_seed, _ = _nearest_road_distance_and_projection(st.pos, runtime_segments)
            if d_seed < max(6.0, 0.45 * float(cfg.local_classic_seed_spacing_m)):
                continue

        trace_stream_attempt_seq += 1
        trace_stream_id = f"local-trace-{int(st.block_idx)}-{trace_stream_attempt_seq}"
        pts = [st.pos]
        prev_dir = st.direction.normalized()
        total_len = 0.0
        branch_role = str(getattr(st, "branch_role", "mainline") or "mainline")
        connected_network_count = 0
        connected_local_count = int(max(0, getattr(st, "local_touch_count", 0) or 0))
        connected_major_count = 0
        cul = False
        reason = "max_steps"
        trace_len_cap = float(block_trace_len_caps[st.block_idx]) if st.block_idx < len(block_trace_len_caps) else float(cfg.local_classic_max_trace_len_m)
        endpoint_span_cap = float(block_endpoint_span_caps[st.block_idx]) if st.block_idx < len(block_endpoint_span_caps) else trace_len_cap * 0.9
        trace_target_enabled = bool(st.block_idx < len(block_trace_target_enabled) and block_trace_target_enabled[st.block_idx])
        trace_target_min_this = float(trace_target_min_m if trace_target_enabled else max(local_spacing_m * 1.8, 220.0))
        trace_target_max_this = float(trace_target_max_m if trace_target_enabled else max(trace_target_min_this + 120.0, min(trace_len_cap, local_spacing_m * 4.6)))
        trace_soft_cap_this = float(min(trace_len_cap, trace_soft_cap_m if trace_target_enabled else trace_len_cap))
        if branch_role == "sub_local_connector":
            trace_target_enabled = False
            trace_target_min_this = float(max(local_spacing_m * 1.4, 160.0))
            trace_target_max_this = float(min(sub_branch_length_cap_m, max(trace_target_min_this + 120.0, local_spacing_m * 6.0)))
            trace_soft_cap_this = float(min(sub_branch_length_cap_m, max(trace_target_max_this + 80.0, local_spacing_m * 7.5)))
            trace_len_cap = float(min(trace_len_cap, sub_branch_length_cap_m))
            endpoint_span_cap = float(min(endpoint_span_cap, trace_len_cap * 0.92))
        elif branch_role == "fill_branch":
            trace_soft_cap_this = float(min(trace_soft_cap_this, trace_len_cap))
        block_long_axis_this = float(block_long_axes[st.block_idx]) if st.block_idx < len(block_long_axes) else 0.0
        near_network_terminate_min_len = max(local_spacing_m * 2.4, 180.0)
        # Treat only the first accepted root trace in a block as the persistent
        # "mainline" trunk. This preserves branch-like continuous growth
        # semantics without making every branch run to the map boundary.
        persistent_mainline = bool(st.depth == 0 and per_block_counts.get(st.block_idx, 0) <= 0)
        step_budget_len = float(max(trace_len_cap, extent_m * 2.4)) if persistent_mainline else float(trace_len_cap)
        max_steps = max(4, int(step_budget_len / step_m) + 2)
        start_pos = pts[0]
        active_block_idx = int(st.block_idx)
        active_block = block
        detach_boost_until_step = -1
        trace_reached_cap = False

        d0, _ = _nearest_road_distance_and_projection(st.pos, base_segments + runtime_segments) if (base_segments or runtime_segments) else (float("inf"), None)
        if d0 < junction_probe:
            connected_network_count += 1
            local_contact_segments = list(existing_local_segments) + list(runtime_segments)
            d0_local, _ = _nearest_road_distance_and_projection(st.pos, local_contact_segments) if local_contact_segments else (float("inf"), None)
            if d0_local < junction_probe:
                connected_local_count += 1
                local_touch_count_total += 1
                if branch_role == "sub_local_connector":
                    local_sub_branch_connector_touch_count += 1
            else:
                d0_major, _ = _nearest_road_distance_and_projection(st.pos, major_segments) if major_segments else (float("inf"), None)
                if d0_major < junction_probe:
                    connected_major_count += 1

        for step_idx in range(max_steps):
            cur = pts[-1]
            slope_deg = probe.sample_slope_deg(cur)
            d_major_cur, _ = _nearest_road_distance_and_projection(cur, major_segments) if major_segments else (float("inf"), None)
            cur_major_clearance = _major_clearance_score(
                cur,
                major_segments,
                influence_radius_m=major_repel_influence_radius_m,
                k=major_clearance_k,
            ) if major_segments else float(detach_target_clearance_m)
            in_post_contact_detach_boost = bool(step_idx < detach_boost_until_step)
            detach_active = bool(
                st.depth <= 1
                and total_len < detach_max_len_m
                and (bool(st.from_major_portal) or in_post_contact_detach_boost or d_major_cur < detach_influence_radius_m)
                and cur_major_clearance < detach_target_clearance_m
            )
            if slope_deg > float(cfg.slope_serpentine_threshold_deg):
                d = probe.choose_serpentine_direction(cur, prev_dir, step_m, rng=rng)
            else:
                d = probe.adjust_direction_for_slope(cur, prev_dir, road_class="local")

            tan_col, d_col = _nearest_segment_tangent(cur, collector_segments)
            if tan_col is not None and d_col < 120.0:
                # local streets often branch roughly perpendicular to collector spines
                spine_prob = float(cfg.local_community_spine_prob)
                if detach_active:
                    spine_prob *= 0.35
                pref = tan_col if rng.random() < spine_prob else _turn_vec(tan_col, 90.0 if rng.random() < 0.5 else -90.0)
                w = min(0.9, float(cfg.local_collector_follow_weight) * (1.0 - d_col / 120.0))
                if detach_active:
                    w = min(w, detach_collector_follow_cap)
                if d.dot(pref) < 0:
                    pref = Vec2(-pref.x, -pref.y)
                d = Vec2(d.x * (1.0 - w) + pref.x * w, d.y * (1.0 - w) + pref.y * w).normalized()

            if branch_role == "sub_local_connector":
                local_endpoint_pool = list(existing_local_endpoints) + list(runtime_local_endpoints)
                local_segment_pool = list(existing_local_segments) + list(runtime_segments)
                seek_target_dir: Optional[Vec2] = None
                endpoint_target_weight = 0.0
                d_ep, ep_target, _ep_lineage = _nearest_endpoint_target(
                    cur,
                    local_endpoint_pool,
                    exclude_lineage=(st.lineage_id or None),
                    max_dist_m=sub_branch_seek_radius_m,
                )
                if ep_target is not None and d_ep > 1e-6:
                    seek_target_dir = (ep_target - cur).normalized()
                    endpoint_target_weight = max(0.0, min(1.0, 1.0 - (d_ep / max(sub_branch_seek_radius_m, 1e-6))))
                if (seek_target_dir is None or seek_target_dir.length() <= 1e-9) and local_segment_pool:
                    d_seg_local, seg_target = _nearest_point_on_segments(cur, local_segment_pool)
                    if seg_target is not None and d_seg_local <= sub_branch_seek_radius_m and d_seg_local > 1e-6:
                        seek_target_dir = (seg_target - cur).normalized()
                        endpoint_target_weight = max(
                            0.0,
                            min(1.0, 0.75 * (1.0 - (d_seg_local / max(sub_branch_seek_radius_m, 1e-6)))),
                        )
                if seek_target_dir is not None and seek_target_dir.length() > 1e-9:
                    if major_segments and d_major_cur < detach_influence_radius_m:
                        rep = _major_repulsion_vector(
                            cur,
                            major_segments,
                            influence_radius_m=major_repel_influence_radius_m,
                            max_samples=major_repel_max_samples,
                        )
                        if rep is not None and rep.length() > 1e-9:
                            # Keep connector-seeking, but avoid long parallel runs hugging major corridors.
                            repel_w = 0.20 + 0.25 * max(0.0, min(1.0, 1.0 - d_major_cur / max(detach_influence_radius_m, 1e-6)))
                            seek_target_dir = Vec2(
                                seek_target_dir.x * (1.0 - repel_w) + rep.x * repel_w,
                                seek_target_dir.y * (1.0 - repel_w) + rep.y * repel_w,
                            ).normalized()
                    seek_w = 0.28 + 0.40 * endpoint_target_weight
                    d = Vec2(
                        d.x * (1.0 - seek_w) + seek_target_dir.x * seek_w,
                        d.y * (1.0 - seek_w) + seek_target_dir.y * seek_w,
                    ).normalized()

            d_base = d.normalized()
            if (
                detach_active
                and major_segments
                and d_base.length() > 1e-9
                and d_major_cur < detach_influence_radius_m
            ):
                major_repel_eval_count += 1
                major_rep_vec = _major_repulsion_vector(
                    cur,
                    major_segments,
                    influence_radius_m=major_repel_influence_radius_m,
                    max_samples=major_repel_max_samples,
                )
                if major_rep_vec is not None and major_rep_vec.length() > 1e-9:
                    closeness = max(0.0, min(1.0, 1.0 - (d_major_cur / max(detach_influence_radius_m, 1e-6))))
                    repel_weight = 0.35 + 0.45 * closeness
                    if in_post_contact_detach_boost:
                        repel_weight = min(0.90, repel_weight + 0.15)
                    d_repel = Vec2(
                        d_base.x * (1.0 - repel_weight) + major_rep_vec.x * repel_weight,
                        d_base.y * (1.0 - repel_weight) + major_rep_vec.y * repel_weight,
                    ).normalized()
                    candidate_dirs = [
                        d_repel,
                        _turn_vec(d_repel, 20.0),
                        _turn_vec(d_repel, -20.0),
                        _turn_vec(d_repel, 40.0),
                        _turn_vec(d_repel, -40.0),
                        d_base,
                        _turn_vec(d_base, 20.0),
                        _turn_vec(d_base, -20.0),
                    ]
                    tan_col_unit = tan_col.normalized() if (tan_col is not None and tan_col.length() > 1e-9) else None
                    best_dir: Optional[Vec2] = None
                    best_score = -1e18
                    best_gain = 0.0
                    for cand0 in candidate_dirs:
                        cand = _clamp_turn(prev_dir, cand0, float(cfg.local_classic_turn_limit_deg))
                        if cand.length() <= 1e-9:
                            continue
                        cand_nxt = Vec2(cur.x + cand.x * step_m, cur.y + cand.y * step_m)
                        if not (0.0 <= cand_nxt.x <= extent_m and 0.0 <= cand_nxt.y <= extent_m):
                            continue
                        if probe.check_water_hit(cand_nxt):
                            continue
                        if not _point_in_poly_or_close(active_block, cand_nxt, tol=1.0):
                            if _find_block_index_for_point(blocks, cand_nxt, preferred_idx=active_block_idx, tol=1.0) is None:
                                continue
                        cand_clear = _major_clearance_score(
                            cand_nxt,
                            major_segments,
                            influence_radius_m=major_repel_influence_radius_m,
                            k=major_clearance_k,
                        )
                        clearance_gain = float(cand_clear - cur_major_clearance)
                        terrain_align = float(cand.dot(d_base))
                        collector_pref_term = 0.0
                        if tan_col_unit is not None and d_col < 120.0:
                            tangentiality = abs(float(cand.dot(tan_col_unit)))
                            collector_pref_term -= 0.35 * max(0.0, tangentiality - 0.35)
                            if abs(float(major_rep_vec.dot(tan_col_unit))) > 0.75:
                                collector_pref_term -= 0.15 * tangentiality
                        score = terrain_align + collector_pref_term + (lambda_clearance * max(0.0, clearance_gain))
                        if score > best_score:
                            best_score = score
                            best_dir = cand
                            best_gain = clearance_gain
                    if best_dir is None:
                        major_repel_no_valid_candidate_count += 1
                    elif best_gain >= clearance_gain_min_m:
                        d = best_dir
                        major_repel_apply_count += 1
                        major_repel_clearance_gain_sum_m += float(max(0.0, best_gain))
                        if in_post_contact_detach_boost:
                            major_repel_post_contact_boost_count += 1

            d = _clamp_turn(prev_dir, d, float(cfg.local_classic_turn_limit_deg))
            if d.length() <= 1e-9:
                reason = "zero_dir"
                break
            nxt = Vec2(cur.x + d.x * step_m, cur.y + d.y * step_m)
            if not (0.0 <= nxt.x <= extent_m and 0.0 <= nxt.y <= extent_m):
                reason = "boundary"
                break
            if not _point_in_poly_or_close(active_block, nxt, tol=1.0):
                next_block_idx = _find_block_index_for_point(blocks, nxt, preferred_idx=active_block_idx, tol=1.0)
                if next_block_idx is None:
                    reason = "block_exit"
                    break
                active_block_idx = int(next_block_idx)
                active_block = blocks[active_block_idx]
            if probe.check_water_hit(nxt):
                alt = probe.snap_or_bias_to_riverfront(cur, d)
                alt = _clamp_turn(prev_dir, alt, float(cfg.local_classic_turn_limit_deg))
                alt_nxt = Vec2(cur.x + alt.x * step_m, cur.y + alt.y * step_m)
                if alt.length() <= 1e-9 or probe.check_water_hit(alt_nxt):
                    reason = "river_blocked"
                    break
                if not _point_in_poly_or_close(active_block, alt_nxt, tol=1.0):
                    alt_block_idx = _find_block_index_for_point(blocks, alt_nxt, preferred_idx=active_block_idx, tol=1.0)
                    if alt_block_idx is None:
                        reason = "river_blocked"
                        break
                    active_block_idx = int(alt_block_idx)
                    active_block = blocks[active_block_idx]
                d, nxt = alt, alt_nxt

            # Soft distance-from-higher-order-roads signal: no hard stop in
            # coverage-first mode. We still use this distance downstream to
            # damp branching/continuation probability.
            max_road_dist = float(cfg.local_classic_max_road_distance_m)

            seg = Segment(cur, nxt)
            if len(pts) >= 4:
                bad = False
                for i in range(len(pts) - 3):
                    if segment_intersection(seg, Segment(pts[i], pts[i + 1])).kind in ("point", "overlap"):
                        bad = True
                        break
                if bad:
                    reason = "self_intersection"
                    break

            d_net, proj_net = _nearest_road_distance_and_projection(nxt, base_segments + runtime_segments) if (base_segments or runtime_segments) else (float("inf"), None)
            # Angle-aware network contact:
            # - head-on / near-parallel contacts merge and stop
            # - perpendicular contacts allow T/cross while the mainline keeps extending
            if d_net < junction_probe and proj_net is not None and total_len >= max(12.0, step_m):
                snapped_to_network = False
                proj_block_idx = _find_block_index_for_point(blocks, proj_net, preferred_idx=active_block_idx, tol=1.0)
                contact_mode = _classify_network_contact_mode(
                    approach_dir=d,
                    contact_point=proj_net,
                    candidate_segments=base_segments + runtime_segments,
                )
                if proj_block_idx is not None and proj_net.distance_to(cur) > 1.5:
                    pts.append(proj_net)
                    total_len += cur.distance_to(proj_net)
                    connected_network_count += 1
                    local_contact_segments = list(existing_local_segments) + list(runtime_segments)
                    d_local_touch, _ = _nearest_road_distance_and_projection(proj_net, local_contact_segments) if local_contact_segments else (float("inf"), None)
                    if d_local_touch < max(junction_probe * 1.15, 10.0):
                        connected_local_count += 1
                        local_touch_count_total += 1
                        if branch_role == "sub_local_connector":
                            local_sub_branch_connector_touch_count += 1
                    else:
                        d_major_touch, _ = _nearest_road_distance_and_projection(proj_net, major_segments) if major_segments else (float("inf"), None)
                        if d_major_touch < max(junction_probe * 1.15, 10.0):
                            connected_major_count += 1
                    snapped_to_network = True
                    active_block_idx = int(proj_block_idx)
                    active_block = blocks[active_block_idx]
                    snap_dir = (proj_net - cur).normalized()
                    if contact_mode in {"parallel", "opposing"} and snap_dir.length() > 1e-9:
                        prev_dir = snap_dir
                terminate_on_touch = False
                if contact_mode in {"opposing", "parallel"}:
                    contact_mode_counts[str(contact_mode)] = contact_mode_counts.get(str(contact_mode), 0) + 1
                    terminate_on_touch = True
                elif (not persistent_mainline) and total_len >= near_network_terminate_min_len:
                    # Keep legacy-ish behavior for deeper branches to prevent overgrowth.
                    terminate_on_touch = True
                if terminate_on_touch:
                    reason = "near_network"
                    break
                # Mainline-first behavior: perpendicular/oblique touches become
                # internal waypoints so the road can continue through T/crosses.
                if contact_mode == "perpendicular":
                    contact_mode_counts["perpendicular_continue"] = contact_mode_counts.get("perpendicular_continue", 0) + 1
                elif contact_mode == "oblique":
                    contact_mode_counts["oblique_continue"] = contact_mode_counts.get("oblique_continue", 0) + 1
                if contact_mode in {"perpendicular", "oblique"} and st.depth <= 1:
                    detach_boost_until_step = max(detach_boost_until_step, int(step_idx + 1 + post_contact_detach_boost_steps))
                if snapped_to_network:
                    continue

            if (not persistent_mainline) and total_len >= max(float(cfg.local_classic_min_trace_len_m), local_spacing_m * 1.15):
                end_span_candidate = start_pos.distance_to(nxt)
                if end_span_candidate > endpoint_span_cap:
                    reason = "span_cap"
                    break

            pts.append(nxt)
            total_len += cur.distance_to(nxt)
            prev_dir = d

            if (
                branch_role == "mainline"
                and st.depth <= 1
                and (st.depth + 1) <= sub_branch_max_depth
            ):
                while total_len >= float(st.next_sub_branch_trigger_m):
                    local_sub_branch_trigger_count += 1
                    branch_signs = (-1.0, 1.0)
                    for sign in branch_signs:
                        bdir = _turn_vec(d, sign * float(rng.uniform(88.0, 92.0)))
                        if bdir.length() <= 1e-9:
                            continue
                        if sign < 0:
                            local_sub_branch_left_spawn_count += 1
                            role_tag = "L"
                        else:
                            local_sub_branch_right_spawn_count += 1
                            role_tag = "R"
                        heapq.heappush(
                            queue,
                            _State(
                                priority=float(st.depth + 1) + float(rng.uniform(0.05, 0.5)),
                                pos=nxt,
                                direction=bdir,
                                block_idx=active_block_idx,
                                depth=st.depth + 1,
                                lineage_id=(f"{st.lineage_id}.s{role_tag}{branch_enq + 1}" if st.lineage_id else f"b{active_block_idx}.s{role_tag}{branch_enq+1}"),
                                parent_lineage_id=(st.lineage_id or None),
                                from_major_portal=False,
                                branch_role="sub_local_connector",
                                next_sub_branch_trigger_m=float("inf"),
                                local_touch_count=0,
                            ),
                        )
                        branch_enq += 1
                    st.next_sub_branch_trigger_m = float(st.next_sub_branch_trigger_m + _sample_sub_branch_trigger_distance())

            # Stream local trace growth (throttled) so frontend can show live local-road generation.
            if (step_idx % 2) == 0:
                _emit_stream_event(stream_cb, {
                    "event_type": "road_trace_progress",
                    "data": {
                        "trace_id": trace_stream_id,
                        "points": [{"x": p.x, "y": p.y} for p in pts],
                        "complete": False,
                        "road_class": "local",
                        "culdesac": False,
                    },
                })

            end_span = start_pos.distance_to(pts[-1])
            if trace_target_enabled:
                if total_len >= max(trace_target_min_this * 0.8, float(cfg.local_classic_min_trace_len_m) * 2.2):
                    if end_span < max(24.0, 0.20 * total_len):
                        reason = "noodle_curve"
                        break
            elif total_len >= max(float(cfg.local_classic_min_trace_len_m) * 1.6, local_spacing_m * 2.2):
                if end_span < max(10.0, 0.18 * total_len):
                    reason = "noodle_curve"
                    break

            # --- Distance-based grid branching ---
            # Trigger branch evaluation when the trace crosses a grid interval
            # (local_spacing_m). This produces regular T/cross intersections
            # instead of the old random step-based branching.
            prev_len = total_len - step_m
            grid_crossed = int(total_len / local_spacing_m) > int(prev_len / local_spacing_m)

            if grid_crossed and st.depth < 5:
                # Slightly suppress branching urge so traces can span further before fractalizing.
                bp = float(cfg.local_classic_branch_prob) * 0.8
                if slope_deg > float(cfg.slope_serpentine_threshold_deg):
                    bp *= 0.5
                depth_decay = max(0.0, (1.0 - float(st.depth) / 6.0)) ** float(cfg.local_classic_depth_decay_power)
                bp *= depth_decay
                if max_road_dist > 0.0 and (arterial_segments or collector_segments):
                    d_branch_check, _ = _nearest_road_distance_and_projection(nxt, arterial_segments + collector_segments)
                    if d_branch_check > 0.4 * max_road_dist:
                        dist_ratio = min(1.0, d_branch_check / max_road_dist)
                        bp *= max(0.1, 1.0 - dist_ratio)
                if rng.random() < bp:
                    # Urban morphology heuristic: mostly T-junctions, fewer 4-way crosses.
                    if rng.random() < 0.30:
                        signs = [-1.0, 1.0]
                    else:
                        signs = [1.0 if rng.random() < 0.5 else -1.0]
                    for sign in signs:
                        # Strong orthogonal constraint to avoid chaotic branch angles.
                        bdir = _turn_vec(d, sign * float(rng.uniform(86.0, 94.0)))
                        if bdir.length() <= 1e-9:
                            continue
                        heapq.heappush(
                            queue,
                            _State(
                                priority=float(st.depth + 1) + float(rng.uniform(0.0, 0.4)),
                                pos=nxt,
                                direction=bdir,
                                block_idx=active_block_idx,
                                depth=st.depth + 1,
                                lineage_id=(f"{st.lineage_id}.b{branch_enq + 1}" if st.lineage_id else f"b{active_block_idx}.d{st.depth+1}.{branch_enq+1}"),
                                parent_lineage_id=(st.lineage_id or None),
                                from_major_portal=bool(st.from_major_portal),
                                branch_role="fill_branch",
                                next_sub_branch_trigger_m=float("inf"),
                                local_touch_count=0,
                            ),
                        )
                        branch_enq += 1

            if total_len >= float(cfg.local_classic_min_trace_len_m):
                if branch_role == "mainline" and total_len >= float(local_trace_hard_cap_m):
                    trace_reached_cap = True
                    reason = "max_len"
                    break
                if persistent_mainline:
                    if total_len >= max(trace_soft_cap_this, min(float(local_trace_hard_cap_m), float(extent_m) * 6.5)):
                        reason = "max_len"
                        break
                else:
                    local_cont_prob = float(cfg.local_classic_continue_prob)
                    # Distance-based continue probability decay for natural edge
                    # thinning: roads far from higher-order network terminate sooner.
                    if max_road_dist > 0.0 and (arterial_segments or collector_segments):
                        d_cont_check, _ = _nearest_road_distance_and_projection(cur, arterial_segments + collector_segments)
                        if d_cont_check > 0.4 * max_road_dist:
                            dist_ratio = min(1.0, d_cont_check / max_road_dist)
                            local_cont_prob *= max(0.15, 1.0 - dist_ratio)
                    if trace_len_cap > max(float(cfg.local_classic_min_trace_len_m) + 1.0, 1.0):
                        trace_ratio = min(1.4, total_len / max(trace_len_cap, 1e-6))
                        if trace_ratio > 0.75:
                            local_cont_prob *= max(0.35, 1.0 - (trace_ratio - 0.75) / 0.65)
                    # Lifespan protection: ensure local roads survive long enough
                    # to cross a typical block (~150m) before stochastic termination.
                    safe_length = max(local_spacing_m * 2.6, 260.0)
                    if total_len < safe_length:
                        progress = total_len / safe_length
                        local_cont_prob = max(local_cont_prob, 0.98 - 0.20 * progress)
                    if rng.random() > local_cont_prob:
                        cul = rng.random() < float(cfg.local_classic_culdesac_prob)
                        reason = "stochastic_stop"
                        break
            if (not persistent_mainline) and total_len >= trace_soft_cap_this:
                reason = "max_len"
                if branch_role == "mainline" and total_len >= float(local_trace_hard_cap_m) - max(step_m * 1.5, 12.0):
                    trace_reached_cap = True
                break

        if len(pts) >= 2 and pts[-1].distance_to(pts[-2]) <= 1e-6:
            pts = pts[:-1]
        if len(pts) < 2:
            continue
        if _polyline_length(pts) < float(cfg.local_classic_min_trace_len_m):
            continue
        # local streets can be semi-disconnected visually inside blocks, but prefer some network relation.
        if connected_network_count < 1:
            d_end, _ = _nearest_road_distance_and_projection(pts[-1], base_segments + runtime_segments) if (base_segments or runtime_segments) else (float("inf"), None)
            if d_end < max(junction_probe * 2.5, 24.0):
                connected_network_count = 1
        local_contact_segments_final = list(existing_local_segments) + list(runtime_segments)
        if connected_local_count < 1 and local_contact_segments_final:
            d_end_local, _ = _nearest_road_distance_and_projection(pts[-1], local_contact_segments_final)
            if d_end_local < max(junction_probe * 2.5, 24.0):
                connected_local_count = 1
                local_touch_count_total += 1
                if branch_role == "sub_local_connector":
                    local_sub_branch_connector_touch_count += 1
        if connected_network_count < 1 and len(runtime_segments) > 0 and (not bool(getattr(cfg, "local_allow_disconnected_accept", False))):
            continue

        traces.append(pts)
        cul_flags.append(bool(cul))
        # Stream completed local trace
        _emit_stream_event(stream_cb, {
            "event_type": "road_trace_progress",
            "data": {
                "trace_id": trace_stream_id,
                "points": [{"x": p.x, "y": p.y} for p in pts],
                "complete": True,
                "road_class": "local",
                "culdesac": bool(cul),
            },
        })
        d_col_end, _ = _nearest_road_distance_and_projection(pts[-1], collector_segments) if collector_segments else (float("inf"), None)
        d_col_start, _ = _nearest_road_distance_and_projection(pts[0], collector_segments) if collector_segments else (float("inf"), None)
        connected_to_collector = bool(min(d_col_start, d_col_end) < max(junction_probe * 2.0, 26.0))
        trace_len = _polyline_length(pts)
        if trace_len >= float(local_trace_hard_cap_m) - max(step_m * 1.5, 12.0):
            local_trace_over_6km_count += 1
        if bool(trace_reached_cap):
            local_trace_reached_cap_count += 1
        is_spine_candidate = bool(
            (not cul)
            and st.depth <= 1
            and trace_len >= max(float(cfg.local_classic_min_trace_len_m) * 1.35, 72.0)
        )
        is_overlimit_unconnected_candidate = bool(
            branch_role == "mainline"
            and trace_reached_cap
            and trace_len >= float(local_trace_hard_cap_m) - max(step_m * 1.5, 12.0)
            and connected_local_count <= 0
        )
        if is_overlimit_unconnected_candidate:
            local_trace_overlimit_unconnected_count += 1
        trace_meta.append(
            LocalTraceMeta(
                block_idx=int(active_block_idx),
                is_spine_candidate=is_spine_candidate,
                connected_to_collector=connected_to_collector,
                culdesac=bool(cul),
                depth=int(st.depth),
                trace_lineage_id=(st.lineage_id or None),
                parent_trace_lineage_id=(st.parent_lineage_id or None),
                minor_local_continuity_id=(st.lineage_id or None),
                parent_minor_local_continuity_id=(st.parent_lineage_id or None),
                seed_origin=(
                    "sub_local_scheduler"
                    if branch_role == "sub_local_connector"
                    else ("major_portal_seed" if bool(getattr(st, "from_major_portal", False)) else "fallback_centroid_seed")
                ),
                branch_role=branch_role,
                trace_len_m=float(trace_len),
                local_touch_count=int(connected_local_count),
                reached_trace_cap=bool(trace_reached_cap),
                terminal_stop_reason=str(reason),
                is_overlimit_unconnected_candidate=bool(is_overlimit_unconnected_candidate),
            )
        )
        if cul:
            cul_count += 1
        runtime_segments.extend(_polyline_segments(pts))
        runtime_local_endpoints.append((pts[0], st.lineage_id or None))
        runtime_local_endpoints.append((pts[-1], st.lineage_id or None))
        per_block_counts[st.block_idx] += 1
        stop_reasons[reason] = stop_reasons.get(reason, 0) + 1
        accepted_trace_lengths.append(float(trace_len))
        accepted_trace_stop_reasons.append(str(reason))
        accepted_trace_block_indices.append(int(st.block_idx))
        accepted_trace_cul_flags.append(bool(cul))

    for reason, count in sorted(stop_reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:6]:
        notes.append(f"local_classic_stop:{reason}:{count}")
    contact_parts = [
        f"{name}={int(contact_mode_counts.get(name, 0))}"
        for name in ("opposing", "parallel", "perpendicular_continue", "oblique_continue")
        if int(contact_mode_counts.get(name, 0)) > 0
    ]
    if contact_parts:
        notes.append("local_classic_contacts:" + ",".join(contact_parts))
    notes.append(f"local_classic_major_portal_seeds:{int(major_seed_source_count)}")
    if fallback_centroid_seed_count > 0:
        notes.append(f"local_classic_fallback_centroid_seeds:{int(fallback_centroid_seed_count)}")
    notes.append(f"local_classic_major_seed_spacing_range_m:{int(round(major_seed_spacing_min_m))}-{int(round(major_seed_spacing_max_m))}")
    notes.append(f"local_classic_trace_count:{len(traces)}")
    notes.append(f"minor_local_run_count:{len(traces)}")
    if trace_cap_n > 0:
        notes.append(f"local_classic_avg_trace_cap_m:{int(round(trace_cap_sum / trace_cap_n))}")
    trace_len_p50 = _quantile(accepted_trace_lengths, 0.50)
    trace_len_p90 = _quantile(accepted_trace_lengths, 0.90)
    trace_len_p99 = _quantile(accepted_trace_lengths, 0.99)
    short_count = sum(1 for v in accepted_trace_lengths if v < trace_target_min_m)
    band_count = sum(1 for v in accepted_trace_lengths if trace_target_min_m <= v <= trace_target_max_m)
    long_count = sum(1 for v in accepted_trace_lengths if v > trace_target_max_m)
    over_1km_count = sum(1 for v in accepted_trace_lengths if v > 1000.0)
    over_3km_count = sum(1 for v in accepted_trace_lengths if v > 3000.0)
    cul_short_count = sum(1 for v, cul in zip(accepted_trace_lengths, accepted_trace_cul_flags) if cul and v < trace_target_min_m)
    cul_total = sum(1 for cul in accepted_trace_cul_flags if cul)
    nonexception_idx: list[int] = []
    exception_reasons = {"river_blocked", "block_exit", "near_network", "road_too_far", "boundary"}
    for i, (length_m, stop_reason, block_idx, cul) in enumerate(
        zip(accepted_trace_lengths, accepted_trace_stop_reasons, accepted_trace_block_indices, accepted_trace_cul_flags)
    ):
        _ = length_m
        bmax = float(block_long_axes[block_idx]) if 0 <= block_idx < len(block_long_axes) else 0.0
        if cul:
            continue
        if bmax < small_block_exception_long_axis_m:
            continue
        if str(stop_reason) in exception_reasons:
            continue
        nonexception_idx.append(i)
    nonexception_lengths = [accepted_trace_lengths[i] for i in nonexception_idx]
    nonexception_band_count = sum(1 for v in nonexception_lengths if trace_target_min_m <= v <= trace_target_max_m)
    if accepted_trace_lengths:
        notes.append(
            "local_classic_trace_len_m:"
            f"p50={int(round(trace_len_p50))},p90={int(round(trace_len_p90))},p99={int(round(trace_len_p99))}"
        )
        notes.append(
            "local_classic_long_trace_rates:"
            f">1km={over_1km_count/len(accepted_trace_lengths):.2f},"
            f">3km={over_3km_count/len(accepted_trace_lengths):.2f},"
            f"reached_6km={local_trace_over_6km_count/len(accepted_trace_lengths):.2f}"
        )
        notes.append(
            "local_classic_trace_target_rates:"
            f"short={short_count/len(accepted_trace_lengths):.2f},"
            f"band={band_count/len(accepted_trace_lengths):.2f},"
            f"long={long_count/len(accepted_trace_lengths):.2f}"
        )
    if local_sub_branch_trigger_count > 0:
        notes.append(
            "local_classic_sub_branches_200_400m:"
            f"triggers={int(local_sub_branch_trigger_count)},"
            f"left={int(local_sub_branch_left_spawn_count)},"
            f"right={int(local_sub_branch_right_spawn_count)},"
            f"connector_touches={int(local_sub_branch_connector_touch_count)}"
        )
    if local_trace_overlimit_unconnected_count > 0:
        notes.append(f"local_classic_overlimit_unconnected_traces:{int(local_trace_overlimit_unconnected_count)}")
    if nonexception_lengths:
        notes.append(
            "local_classic_trace_nonexception_band_rate:"
            f"{nonexception_band_count/len(nonexception_lengths):.2f}"
        )
    numeric = {
        "local_classic_enabled": 1.0,
        "minor_local_run_generator_enabled": 1.0,
        "local_classic_trace_count": float(len(traces)),
        "minor_local_run_count": float(len(traces)),
        "local_classic_culdesac_count": float(cul_count),
        "local_classic_branch_enqueued_count": float(branch_enq),
        "local_classic_avg_trace_cap_m": float(trace_cap_sum / trace_cap_n) if trace_cap_n > 0 else 0.0,
        "local_classic_trace_len_p50_m": float(trace_len_p50),
        "local_classic_trace_len_p90_m": float(trace_len_p90),
        "local_classic_trace_len_p99_m": float(trace_len_p99),
        "local_classic_long_trace_cap_m": float(local_trace_hard_cap_m),
        "local_minor_run_hard_cap_m": float(local_trace_hard_cap_m),
        "local_classic_trace_short_rate": float(short_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_target_band_rate": float(band_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_long_rate": float(long_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_over_1km_rate": float(over_1km_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_over_3km_rate": float(over_3km_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_over_6km_count": float(local_trace_over_6km_count),
        "local_classic_trace_reached_cap_count": float(local_trace_reached_cap_count),
        "local_classic_trace_overlimit_unconnected_count": float(local_trace_overlimit_unconnected_count),
        "local_classic_trace_culdesac_short_rate": float(cul_short_count / cul_total) if cul_total > 0 else 0.0,
        "local_classic_trace_nonexception_target_band_rate": (
            float(nonexception_band_count / len(nonexception_lengths)) if nonexception_lengths else 0.0
        ),
        "local_classic_contact_opposing_count": float(contact_mode_counts.get("opposing", 0)),
        "local_classic_contact_parallel_count": float(contact_mode_counts.get("parallel", 0)),
        "local_classic_contact_perpendicular_continue_count": float(contact_mode_counts.get("perpendicular_continue", 0)),
        "local_classic_contact_oblique_continue_count": float(contact_mode_counts.get("oblique_continue", 0)),
        "local_classic_major_portal_seed_count": float(major_seed_source_count),
        "local_classic_fallback_centroid_seed_count": float(fallback_centroid_seed_count),
        "local_classic_major_seed_spacing_target_min_m": float(major_seed_spacing_min_m),
        "local_classic_major_seed_spacing_target_max_m": float(major_seed_spacing_max_m),
        "local_classic_major_seed_spacing_interval_obs_min_m": (
            float(min(portal_interval_samples_m)) if portal_interval_samples_m else 0.0
        ),
        "local_classic_major_seed_spacing_interval_obs_max_m": (
            float(max(portal_interval_samples_m)) if portal_interval_samples_m else 0.0
        ),
        "local_classic_major_repel_eval_count": float(major_repel_eval_count),
        "local_classic_major_repel_apply_count": float(major_repel_apply_count),
        "local_classic_major_repel_post_contact_boost_count": float(major_repel_post_contact_boost_count),
        "local_classic_major_repel_no_valid_candidate_count": float(major_repel_no_valid_candidate_count),
        "local_classic_major_repel_clearance_gain_sum_m": float(major_repel_clearance_gain_sum_m),
        "local_classic_major_repel_clearance_gain_avg_m": (
            float(major_repel_clearance_gain_sum_m / major_repel_apply_count) if major_repel_apply_count > 0 else 0.0
        ),
        "local_classic_local_touch_count_total": float(local_touch_count_total),
        "local_classic_sub_branch_trigger_count": float(local_sub_branch_trigger_count),
        "local_classic_sub_branch_left_spawn_count": float(local_sub_branch_left_spawn_count),
        "local_classic_sub_branch_right_spawn_count": float(local_sub_branch_right_spawn_count),
        "local_classic_sub_branch_connector_touch_count": float(local_sub_branch_connector_touch_count),
    }
    # Promote stop-reason diagnostics to numeric metrics so callers can track
    # coverage regressions without parsing notes strings.
    for stop_key in (
        "near_network",
        "block_exit",
        "stochastic_stop",
        "road_too_far",
        "river_blocked",
        "span_cap",
    ):
        numeric[f"local_classic_stop_{stop_key}_count"] = float(stop_reasons.get(stop_key, 0))
    return traces, cul_flags, trace_meta, notes, numeric
