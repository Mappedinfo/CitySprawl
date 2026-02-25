from __future__ import annotations

from dataclasses import dataclass
import heapq
from collections import defaultdict
from typing import Optional, Sequence

import numpy as np

from engine.core.geometry import Segment, Vec2, segment_intersection
from engine.roads.classic_growth import (
    _clamp_turn,
    _flatten_segments_from_edges,
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
    # Soft target for semantic local traces (not final topology-split edges).
    local_trace_target_min_m: float = 500.0
    local_trace_target_max_m: float = 1000.0
    local_trace_soft_cap_m: float = 1200.0
    local_trace_force_continue_until_min: bool = True
    local_trace_exception_small_block_long_axis_m: float = 650.0
    local_classic_probe_step_m: float = 18.0
    local_classic_seed_spacing_m: float = 110.0
    local_classic_max_trace_len_m: float = 420.0
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


@dataclass
class LocalTraceMeta:
    block_idx: int
    is_spine_candidate: bool = False
    connected_to_collector: bool = False
    culdesac: bool = False


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
) -> tuple[list[list[Vec2]], list[bool], list[LocalTraceMeta], list[str], dict[str, float]]:
    rng = np.random.default_rng(int(seed) + 9203)
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
    higher_order_segments = list(arterial_segments) + list(collector_segments)
    runtime_segments: list[Segment] = []

    queue: list[_State] = []
    block_seed_counts: list[int] = []
    block_trace_len_caps: list[float] = []
    block_endpoint_span_caps: list[float] = []
    block_long_axes: list[float] = []
    block_trace_target_enabled: list[bool] = []
    local_spacing_m = max(24.0, float(getattr(cfg, "local_spacing_m", 130.0) or 130.0))
    trace_target_min_m = max(120.0, float(getattr(cfg, "local_trace_target_min_m", 500.0) or 500.0))
    trace_target_max_m = max(trace_target_min_m + 80.0, float(getattr(cfg, "local_trace_target_max_m", 1000.0) or 1000.0))
    trace_soft_cap_m = max(trace_target_max_m + 120.0, float(getattr(cfg, "local_trace_soft_cap_m", 1200.0) or 1200.0))
    small_block_exception_long_axis_m = max(
        260.0,
        float(getattr(cfg, "local_trace_exception_small_block_long_axis_m", 650.0) or 650.0),
    )
    force_continue_until_min = bool(getattr(cfg, "local_trace_force_continue_until_min", True))
    trace_cap_sum = 0.0
    trace_cap_n = 0
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
        seeds = _block_centroid_vecs(block, int(max(1, cfg.local_community_seed_count_per_block)), rng)
        major = _unit_from_angle_deg(_major_axis_angle_deg(block))
        added = 0
        for sp in seeds:
            if not _point_in_poly_or_close(block, sp, tol=1.0):
                continue
            if probe.check_water_hit(sp):
                continue
            tan_col, d_col = _nearest_segment_tangent(sp, collector_segments)
            tan_art, d_art = _nearest_segment_tangent(sp, arterial_segments)
            base = tan_col or tan_art or major or _nearest_hub_vector(sp, hubs) or Vec2(1.0, 0.0)
            # Follow collector if close; otherwise orient by block major axis with stronger curvature later.
            if tan_col is not None and d_col < 160.0:
                base = tan_col if rng.random() < float(cfg.local_community_spine_prob) else _turn_vec(tan_col, 90.0 if rng.random() < 0.5 else -90.0)
            elif tan_art is not None and d_art < 200.0:
                perp = _turn_vec(tan_art, 90.0 if rng.random() < 0.5 else -90.0)
                base = tan_art if rng.random() < 0.25 else perp
            d0 = _turn_vec(base, float(rng.uniform(-32.0, 32.0)))
            heapq.heappush(queue, _State(priority=float(rng.uniform(0.0, 0.8)), pos=sp, direction=d0, block_idx=bi, depth=0))
            heapq.heappush(queue, _State(priority=float(rng.uniform(0.2, 1.2)), pos=sp, direction=_turn_vec(d0, 180.0 + float(rng.uniform(-25.0, 25.0))), block_idx=bi, depth=0))
            added += 2
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

    step_m = max(8.0, float(cfg.local_classic_probe_step_m))
    junction_probe = max(8.0, step_m * 0.85)

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

        pts = [st.pos]
        prev_dir = st.direction.normalized()
        total_len = 0.0
        connected = 0
        cul = False
        reason = "max_steps"
        trace_len_cap = float(block_trace_len_caps[st.block_idx]) if st.block_idx < len(block_trace_len_caps) else float(cfg.local_classic_max_trace_len_m)
        endpoint_span_cap = float(block_endpoint_span_caps[st.block_idx]) if st.block_idx < len(block_endpoint_span_caps) else trace_len_cap * 0.9
        trace_target_enabled = bool(st.block_idx < len(block_trace_target_enabled) and block_trace_target_enabled[st.block_idx])
        trace_target_min_this = float(trace_target_min_m if trace_target_enabled else max(local_spacing_m * 1.8, 220.0))
        trace_target_max_this = float(trace_target_max_m if trace_target_enabled else max(trace_target_min_this + 120.0, min(trace_len_cap, local_spacing_m * 4.6)))
        trace_soft_cap_this = float(min(trace_len_cap, trace_soft_cap_m if trace_target_enabled else trace_len_cap))
        block_long_axis_this = float(block_long_axes[st.block_idx]) if st.block_idx < len(block_long_axes) else 0.0
        max_steps = max(4, int(trace_len_cap / step_m) + 2)
        start_pos = pts[0]

        d0, _ = _nearest_road_distance_and_projection(st.pos, base_segments + runtime_segments) if (base_segments or runtime_segments) else (float("inf"), None)
        if d0 < junction_probe:
            connected += 1

        for step_idx in range(max_steps):
            cur = pts[-1]
            slope_deg = probe.sample_slope_deg(cur)
            if slope_deg > float(cfg.slope_serpentine_threshold_deg):
                d = probe.choose_serpentine_direction(cur, prev_dir, step_m, rng=rng)
            else:
                d = probe.adjust_direction_for_slope(cur, prev_dir, road_class="local")

            tan_col, d_col = _nearest_segment_tangent(cur, collector_segments)
            if tan_col is not None and d_col < 120.0:
                # local streets often branch roughly perpendicular to collector spines
                pref = tan_col if rng.random() < float(cfg.local_community_spine_prob) else _turn_vec(tan_col, 90.0 if rng.random() < 0.5 else -90.0)
                w = min(0.9, float(cfg.local_collector_follow_weight) * (1.0 - d_col / 120.0))
                if d.dot(pref) < 0:
                    pref = Vec2(-pref.x, -pref.y)
                d = Vec2(d.x * (1.0 - w) + pref.x * w, d.y * (1.0 - w) + pref.y * w).normalized()

            d = _clamp_turn(prev_dir, d, float(cfg.local_classic_turn_limit_deg))
            if d.length() <= 1e-9:
                reason = "zero_dir"
                break
            nxt = Vec2(cur.x + d.x * step_m, cur.y + d.y * step_m)
            if not (0.0 <= nxt.x <= extent_m and 0.0 <= nxt.y <= extent_m):
                reason = "boundary"
                break
            if not _point_in_poly_or_close(block, nxt, tol=1.0):
                reason = "block_exit"
                break
            if probe.check_water_hit(nxt):
                alt = probe.snap_or_bias_to_riverfront(cur, d)
                alt = _clamp_turn(prev_dir, alt, float(cfg.local_classic_turn_limit_deg))
                alt_nxt = Vec2(cur.x + alt.x * step_m, cur.y + alt.y * step_m)
                if alt.length() <= 1e-9 or probe.check_water_hit(alt_nxt) or not _point_in_poly_or_close(block, alt_nxt, tol=1.0):
                    reason = "river_blocked"
                    break
                d, nxt = alt, alt_nxt

            # Distance-from-higher-order-roads constraint: stop local road
            # growth that wanders too far from arterials/collectors.
            max_road_dist = float(cfg.local_classic_max_road_distance_m)
            if max_road_dist > 0.0 and (arterial_segments or collector_segments):
                d_higher, _ = _nearest_road_distance_and_projection(nxt, arterial_segments + collector_segments)
                if d_higher > max_road_dist:
                    reason = "road_too_far"
                    break

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
            # FIX: Removed trace_target_enabled logic that forced d_net=inf to prevent
            # early termination. Now local roads snap to network as soon as they touch it,
            # enabling proper grid closure and block formation.
            if d_net < junction_probe and proj_net is not None and total_len >= max(12.0, step_m):
                if _point_in_poly_or_close(block, proj_net, tol=1.0) and proj_net.distance_to(cur) > 1.5:
                    pts.append(proj_net)
                    total_len += cur.distance_to(proj_net)
                    connected += 1
                reason = "near_network"
                break

            if total_len >= max(float(cfg.local_classic_min_trace_len_m), local_spacing_m * 1.15):
                end_span_candidate = start_pos.distance_to(nxt)
                if end_span_candidate > endpoint_span_cap:
                    reason = "span_cap"
                    break

            pts.append(nxt)
            total_len += cur.distance_to(nxt)
            prev_dir = d

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

            if step_idx > 0 and (step_idx % 2 == 0) and st.depth < 8:
                bp = float(cfg.local_classic_branch_prob)
                if slope_deg > float(cfg.slope_serpentine_threshold_deg):
                    bp *= 0.8
                # Depth-based probability decay: branches become rarer at
                # higher generation depths to prevent infinite local sprawl.
                depth_decay = max(0.0, (1.0 - float(st.depth) / 8.0)) ** float(cfg.local_classic_depth_decay_power)
                bp *= depth_decay
                # Distance-based branch probability decay: reduce branching
                # far from higher-order roads for natural edge thinning.
                if max_road_dist > 0.0 and (arterial_segments or collector_segments):
                    d_branch_check, _ = _nearest_road_distance_and_projection(nxt, arterial_segments + collector_segments)
                    if d_branch_check > 0.4 * max_road_dist:
                        dist_ratio = min(1.0, d_branch_check / max_road_dist)
                        bp *= max(0.1, 1.0 - dist_ratio)
                # FIX: Removed trace_target_enabled branch suppression that prevented
                # early branching. Now branches can form at normal rates for grid generation.
                if rng.random() < bp:
                    for sign in (-1.0, 1.0):
                        if rng.random() > (0.55 if sign < 0 else 0.75):
                            continue
                        bdir = _turn_vec(d, sign * float(rng.uniform(65.0, 115.0)))
                        if bdir.length() <= 1e-9:
                            continue
                        heapq.heappush(
                            queue,
                            _State(priority=float(st.depth + 1) + float(rng.uniform(0.0, 0.9)), pos=nxt, direction=bdir, block_idx=st.block_idx, depth=st.depth + 1),
                        )
                        branch_enq += 1

            if total_len >= float(cfg.local_classic_min_trace_len_m):
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
                # FIX: Removed trace_target_enabled forced continuation logic that
                # prevented roads from stopping naturally. Now roads can terminate
                # when they should, enabling proper grid closure.
                if rng.random() > local_cont_prob:
                    cul = rng.random() < float(cfg.local_classic_culdesac_prob)
                    reason = "stochastic_stop"
                    break
            if total_len >= trace_soft_cap_this:
                reason = "max_len"
                break

        if len(pts) >= 2 and pts[-1].distance_to(pts[-2]) <= 1e-6:
            pts = pts[:-1]
        if len(pts) < 2:
            continue
        if _polyline_length(pts) < float(cfg.local_classic_min_trace_len_m):
            continue
        # local streets can be semi-disconnected visually inside blocks, but prefer some network relation.
        if connected < 1:
            d_end, _ = _nearest_road_distance_and_projection(pts[-1], base_segments + runtime_segments) if (base_segments or runtime_segments) else (float("inf"), None)
            if d_end < max(junction_probe * 2.5, 24.0):
                connected = 1
        if connected < 1 and len(runtime_segments) > 0:
            continue

        traces.append(pts)
        cul_flags.append(bool(cul))
        d_col_end, _ = _nearest_road_distance_and_projection(pts[-1], collector_segments) if collector_segments else (float("inf"), None)
        d_col_start, _ = _nearest_road_distance_and_projection(pts[0], collector_segments) if collector_segments else (float("inf"), None)
        connected_to_collector = bool(min(d_col_start, d_col_end) < max(junction_probe * 2.0, 26.0))
        trace_len = _polyline_length(pts)
        is_spine_candidate = bool(
            (not cul)
            and st.depth <= 1
            and trace_len >= max(float(cfg.local_classic_min_trace_len_m) * 1.35, 72.0)
        )
        trace_meta.append(
            LocalTraceMeta(
                block_idx=int(st.block_idx),
                is_spine_candidate=is_spine_candidate,
                connected_to_collector=connected_to_collector,
                culdesac=bool(cul),
            )
        )
        if cul:
            cul_count += 1
        runtime_segments.extend(_polyline_segments(pts))
        per_block_counts[st.block_idx] += 1
        stop_reasons[reason] = stop_reasons.get(reason, 0) + 1
        accepted_trace_lengths.append(float(trace_len))
        accepted_trace_stop_reasons.append(str(reason))
        accepted_trace_block_indices.append(int(st.block_idx))
        accepted_trace_cul_flags.append(bool(cul))

    for reason, count in sorted(stop_reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:6]:
        notes.append(f"local_classic_stop:{reason}:{count}")
    notes.append(f"local_classic_trace_count:{len(traces)}")
    if trace_cap_n > 0:
        notes.append(f"local_classic_avg_trace_cap_m:{int(round(trace_cap_sum / trace_cap_n))}")
    trace_len_p50 = _quantile(accepted_trace_lengths, 0.50)
    trace_len_p90 = _quantile(accepted_trace_lengths, 0.90)
    trace_len_p99 = _quantile(accepted_trace_lengths, 0.99)
    short_count = sum(1 for v in accepted_trace_lengths if v < trace_target_min_m)
    band_count = sum(1 for v in accepted_trace_lengths if trace_target_min_m <= v <= trace_target_max_m)
    long_count = sum(1 for v in accepted_trace_lengths if v > trace_target_max_m)
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
            "local_classic_trace_target_rates:"
            f"short={short_count/len(accepted_trace_lengths):.2f},"
            f"band={band_count/len(accepted_trace_lengths):.2f},"
            f"long={long_count/len(accepted_trace_lengths):.2f}"
        )
    if nonexception_lengths:
        notes.append(
            "local_classic_trace_nonexception_band_rate:"
            f"{nonexception_band_count/len(nonexception_lengths):.2f}"
        )
    numeric = {
        "local_classic_enabled": 1.0,
        "local_classic_trace_count": float(len(traces)),
        "local_classic_culdesac_count": float(cul_count),
        "local_classic_branch_enqueued_count": float(branch_enq),
        "local_classic_avg_trace_cap_m": float(trace_cap_sum / trace_cap_n) if trace_cap_n > 0 else 0.0,
        "local_classic_trace_len_p50_m": float(trace_len_p50),
        "local_classic_trace_len_p90_m": float(trace_len_p90),
        "local_classic_trace_len_p99_m": float(trace_len_p99),
        "local_classic_trace_short_rate": float(short_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_target_band_rate": float(band_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_long_rate": float(long_count / len(accepted_trace_lengths)) if accepted_trace_lengths else 0.0,
        "local_classic_trace_culdesac_short_rate": float(cul_short_count / cul_total) if cul_total > 0 else 0.0,
        "local_classic_trace_nonexception_target_band_rate": (
            float(nonexception_band_count / len(nonexception_lengths)) if nonexception_lengths else 0.0
        ),
    }
    return traces, cul_flags, trace_meta, notes, numeric
