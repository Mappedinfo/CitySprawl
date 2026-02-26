from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from engine.core.geometry import Vec2


RouteSegmentFn = Callable[[Vec2, Vec2, Optional[object], float, float], Optional[list[Vec2]]]


@dataclass
class LocalRerouteConfig:
    local_geometry_mode: str = "classic_sprawl_rerouted"
    local_reroute_coverage: str = "selective"
    local_reroute_min_length_m: float = 70.0
    local_reroute_waypoint_spacing_m: float = 26.0
    local_reroute_max_waypoints: int = 16
    local_reroute_corridor_buffer_m: float = 38.0
    local_reroute_block_margin_m: float = 2.0
    local_reroute_slope_penalty_scale: float = 1.15
    local_reroute_river_penalty_scale: float = 1.35
    local_reroute_collector_snap_bias_m: float = 22.0
    local_reroute_smooth_iters: int = 1
    local_reroute_simplify_tol_m: float = 3.0
    local_reroute_max_edges_per_city: int = 180
    local_reroute_apply_to_grid_supplement: bool = True


def _polyline_length(points: Sequence[Vec2]) -> float:
    if len(points) < 2:
        return 0.0
    return float(sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1)))


def _dedupe_points(points: Sequence[Vec2], tol: float = 1e-6) -> list[Vec2]:
    out: list[Vec2] = []
    for p in points:
        if not out or p.distance_to(out[-1]) > tol:
            out.append(p)
    return out


def _sample_waypoints(points: Sequence[Vec2], spacing_m: float, max_waypoints: int) -> list[Vec2]:
    pts = _dedupe_points(points)
    if len(pts) <= 2:
        return pts
    spacing = max(4.0, float(spacing_m))
    total = _polyline_length(pts)
    if total <= spacing:
        return [pts[0], pts[-1]]
    target_n = max(2, min(int(max_waypoints), int(total / spacing) + 2))
    if target_n <= 2:
        return [pts[0], pts[-1]]

    # Evenly sample by arc length.
    segment_lens = [pts[i].distance_to(pts[i + 1]) for i in range(len(pts) - 1)]
    out = [pts[0]]
    for j in range(1, target_n - 1):
        tlen = (total * j) / float(target_n - 1)
        acc = 0.0
        for i, sl in enumerate(segment_lens):
            if sl <= 1e-9:
                continue
            if acc + sl >= tlen:
                lt = (tlen - acc) / sl
                a = pts[i]
                b = pts[i + 1]
                out.append(Vec2(a.x + (b.x - a.x) * lt, a.y + (b.y - a.y) * lt))
                break
            acc += sl
    out.append(pts[-1])
    return _dedupe_points(out)


def _chaikin_once(points: Sequence[Vec2]) -> list[Vec2]:
    pts = _dedupe_points(points)
    if len(pts) <= 2:
        return list(pts)
    out = [pts[0]]
    for i in range(len(pts) - 1):
        a = pts[i]
        b = pts[i + 1]
        q = Vec2(0.75 * a.x + 0.25 * b.x, 0.75 * a.y + 0.25 * b.y)
        r = Vec2(0.25 * a.x + 0.75 * b.x, 0.25 * a.y + 0.75 * b.y)
        out.extend([q, r])
    out.append(pts[-1])
    return _dedupe_points(out)


def _simplify_polyline(points: Sequence[Vec2], tol_m: float) -> list[Vec2]:
    if len(points) <= 2 or tol_m <= 0.0:
        return list(points)
    try:
        from shapely.geometry import LineString  # type: ignore
    except Exception:
        return list(points)
    try:
        geom = LineString([(p.x, p.y) for p in points]).simplify(float(tol_m), preserve_topology=False)
    except Exception:
        return list(points)
    coords = list(getattr(geom, "coords", []))
    if len(coords) < 2:
        return list(points)
    out = [Vec2(float(x), float(y)) for x, y in coords]
    out[0] = points[0]
    out[-1] = points[-1]
    return _dedupe_points(out)


def build_local_routing_corridor(
    trace_points: Sequence[Vec2],
    *,
    block_poly: object | None,
    river_union: object | None,
    corridor_buffer_m: float,
    block_margin_m: float,
    river_setback_m: float,
) -> object | None:
    if len(trace_points) < 2:
        return None
    try:
        from shapely.geometry import LineString  # type: ignore
    except Exception:
        return None
    try:
        line = LineString([(p.x, p.y) for p in trace_points])
        corridor = line.buffer(float(max(4.0, corridor_buffer_m)))
    except Exception:
        return None
    if getattr(corridor, "is_empty", True):
        return None

    if block_poly is not None:
        try:
            inner = block_poly
            if float(block_margin_m) > 0.0:
                shrunk = block_poly.buffer(-float(block_margin_m))
                if not getattr(shrunk, "is_empty", True):
                    inner = shrunk
            corridor = corridor.intersection(inner)
        except Exception:
            pass
    if river_union is not None and float(river_setback_m) > 0.0:
        try:
            corridor = corridor.difference(river_union.buffer(float(river_setback_m)))
        except Exception:
            pass
    if getattr(corridor, "is_empty", True):
        return None
    return corridor


def route_polyline_through_waypoints(
    waypoints: Sequence[Vec2],
    *,
    corridor_geom: object | None,
    route_segment_fn: RouteSegmentFn,
    slope_penalty_scale: float,
    river_penalty_scale: float,
) -> Optional[list[Vec2]]:
    if len(waypoints) < 2:
        return None
    stitched: list[Vec2] = []
    for i in range(len(waypoints) - 1):
        seg_pts = route_segment_fn(
            waypoints[i],
            waypoints[i + 1],
            corridor_geom,
            float(slope_penalty_scale),
            float(river_penalty_scale),
        )
        if not seg_pts or len(seg_pts) < 2:
            return None
        if not stitched:
            stitched.extend(seg_pts)
        else:
            stitched.extend(seg_pts[1:])
    return _dedupe_points(stitched)


def reroute_local_polyline(
    trace_points: Sequence[Vec2],
    *,
    route_segment_fn: RouteSegmentFn,
    cfg: LocalRerouteConfig,
    block_poly: object | None = None,
    river_union: object | None = None,
    river_setback_m: float = 0.0,
) -> tuple[list[Vec2], dict[str, float], list[str]]:
    original = _dedupe_points(trace_points)
    if len(original) < 2:
        return list(original), {"applied": 0.0, "fallback": 1.0, "waypoint_count": 0.0}, ["local_reroute:fallback_short"]
    notes: list[str] = []
    numeric: dict[str, float] = {
        "applied": 0.0,
        "fallback": 0.0,
        "waypoint_count": 0.0,
        "path_points": float(len(original)),
        "length_gain_ratio": 1.0,
    }
    if str(cfg.local_geometry_mode).lower() == "trace_direct":
        notes.append("local_reroute:disabled_trace_direct")
        return list(original), numeric, notes

    waypoints = _sample_waypoints(
        original,
        spacing_m=float(cfg.local_reroute_waypoint_spacing_m),
        max_waypoints=int(cfg.local_reroute_max_waypoints),
    )
    numeric["waypoint_count"] = float(len(waypoints))
    corridor = build_local_routing_corridor(
        original,
        block_poly=block_poly,
        river_union=river_union,
        corridor_buffer_m=float(cfg.local_reroute_corridor_buffer_m),
        block_margin_m=float(cfg.local_reroute_block_margin_m),
        river_setback_m=float(river_setback_m),
    )
    rerouted = route_polyline_through_waypoints(
        waypoints,
        corridor_geom=corridor,
        route_segment_fn=route_segment_fn,
        slope_penalty_scale=float(cfg.local_reroute_slope_penalty_scale),
        river_penalty_scale=float(cfg.local_reroute_river_penalty_scale),
    )
    if not rerouted or len(rerouted) < 2:
        numeric["fallback"] = 1.0
        notes.append("local_reroute:fallback_route_failed")
        return list(original), numeric, notes

    out = _simplify_polyline(rerouted, float(cfg.local_reroute_simplify_tol_m))
    for _ in range(max(0, int(cfg.local_reroute_smooth_iters))):
        out = _chaikin_once(out)
        if len(out) >= 2:
            out[0] = original[0]
            out[-1] = original[-1]
    out = _dedupe_points(out)
    if len(out) < 2:
        numeric["fallback"] = 1.0
        notes.append("local_reroute:fallback_postprocess")
        return list(original), numeric, notes

    orig_len = max(_polyline_length(original), 1e-6)
    new_len = _polyline_length(out)
    numeric["applied"] = 1.0
    numeric["path_points"] = float(len(out))
    numeric["length_gain_ratio"] = float(new_len / orig_len)
    notes.append("local_reroute:applied")
    return out, numeric, notes


def select_local_reroute_candidates(
    items: Sequence[object],
    *,
    coverage: str,
    min_length_m: float,
    max_edges: int,
    apply_to_grid_supplement: bool,
) -> list[int]:
    cov = str(coverage or "selective").lower()
    scored: list[tuple[int, int]] = []
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            road_class = str(item.get("road_class", ""))
        else:
            road_class = str(getattr(item, "road_class", ""))
        if road_class and road_class != "minor_local":
            continue
        flags = set(item.get("flags", set()) or set()) if isinstance(item, dict) else set(getattr(item, "flags", set()) or set())
        length_m = float(item.get("length_m", 0.0)) if isinstance(item, dict) else float(getattr(item, "length_m", 0.0))
        if "culdesac" in flags and length_m < max(min_length_m * 1.2, 120.0):
            continue
        meta = item.get("meta", None) if isinstance(item, dict) else getattr(item, "meta", None)
        is_grid = bool(item.get("is_grid_supplement", False)) if isinstance(item, dict) else bool(getattr(item, "is_grid_supplement", False))
        if is_grid and not apply_to_grid_supplement:
            continue
        connected_to_collector = bool(getattr(meta, "connected_to_collector", False)) if meta is not None else False
        is_spine = bool(getattr(meta, "is_spine_candidate", False)) if meta is not None else False
        if isinstance(meta, dict):
            connected_to_collector = bool(meta.get("connected_to_collector", connected_to_collector))
            is_spine = bool(meta.get("is_spine_candidate", is_spine))

        if cov == "connectors_only" and not connected_to_collector:
            continue
        if cov == "selective":
            if not (length_m >= float(min_length_m) or connected_to_collector or is_spine):
                continue
        # Priority: connectors > spines > length > grid supplement
        score = 0
        if connected_to_collector:
            score += 3000
        if is_spine:
            score += 2000
        score += int(min(1000, length_m))
        if is_grid:
            score -= 250
        scored.append((score, idx))
    scored.sort(key=lambda x: (-x[0], x[1]))
    limit = max(0, int(max_edges))
    return [idx for _, idx in scored[:limit]] if limit else []
