from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, exp, hypot, radians, sin
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from engine.core.geometry import Segment, Vec2, point_segment_distance, project_point_to_segment, segment_intersection


@dataclass
class TensorStreamlineConfig:
    tensor_grid_resolution: int = 96
    tensor_step_m: float = 24.0
    tensor_seed_spacing_m: float = 260.0
    tensor_max_trace_len_m: float = 1800.0
    tensor_min_trace_len_m: float = 120.0
    tensor_turn_limit_deg: float = 38.0
    tensor_water_tangent_weight: float = 1.15
    tensor_contour_tangent_weight: float = 0.95
    tensor_arterial_align_weight: float = 0.70
    tensor_hub_attract_weight: float = 0.35
    tensor_water_influence_m: float = 320.0
    tensor_arterial_influence_m: float = 380.0
    river_setback_m: float = 18.0


@dataclass
class TensorFieldGrid:
    extent_m: float
    resolution: int
    tensor_xx: np.ndarray
    tensor_xy: np.ndarray
    tensor_yy: np.ndarray
    strength: np.ndarray

    def _world_to_cell(self, p: Vec2) -> tuple[int, int]:
        if self.resolution <= 1:
            return (0, 0)
        x = int(round((float(p.x) / max(float(self.extent_m), 1e-9)) * (self.resolution - 1)))
        y = int(round((float(p.y) / max(float(self.extent_m), 1e-9)) * (self.resolution - 1)))
        x = min(max(x, 0), self.resolution - 1)
        y = min(max(y, 0), self.resolution - 1)
        return (x, y)

    def _eigen_dirs(self, p: Vec2) -> tuple[Vec2, Vec2, float]:
        x, y = self._world_to_cell(p)
        a = float(self.tensor_xx[y, x])
        b = float(self.tensor_xy[y, x])
        c = float(self.tensor_yy[y, x])
        # Principal direction angle for 2x2 symmetric tensor
        theta = 0.5 * atan2(2.0 * b, a - c)
        major = Vec2(cos(theta), sin(theta)).normalized()
        minor = Vec2(-major.y, major.x)
        s = float(self.strength[y, x]) if self.strength.size else 0.0
        return major, minor, s

    def sample_major_dir(self, p: Vec2) -> Vec2:
        return self._eigen_dirs(p)[0]

    def sample_minor_dir(self, p: Vec2) -> Vec2:
        return self._eigen_dirs(p)[1]

    def sample_strength(self, p: Vec2) -> float:
        return self._eigen_dirs(p)[2]


@dataclass
class TraceResult:
    points: List[Vec2]
    connection_count: int
    reason: str


def _iter_polyline_points(edge: object, node_lookup: dict[str, Vec2]) -> list[Vec2]:
    path = getattr(edge, "path_points", None)
    if path and len(path) >= 2:
        out = []
        for p in path:
            x = p.x if hasattr(p, "x") else p["x"]
            y = p.y if hasattr(p, "y") else p["y"]
            out.append(Vec2(float(x), float(y)))
        if len(out) >= 2:
            return out
    u = node_lookup.get(str(getattr(edge, "u", "")))
    v = node_lookup.get(str(getattr(edge, "v", "")))
    if u is None or v is None:
        return []
    return [u, v]


def _polyline_segments(points: Sequence[Vec2]) -> list[Segment]:
    return [Segment(points[i], points[i + 1]) for i in range(len(points) - 1) if points[i].distance_to(points[i + 1]) > 1e-6]


def _flatten_segments_from_edges(
    edges: Sequence[object],
    nodes: Sequence[object],
    road_classes: Optional[set[str]] = None,
) -> list[Segment]:
    node_lookup = {str(getattr(n, "id")): Vec2(float(getattr(n, "pos").x), float(getattr(n, "pos").y)) for n in nodes if hasattr(n, "pos")}
    segs: list[Segment] = []
    for e in edges:
        rc = str(getattr(e, "road_class", ""))
        if road_classes is not None and rc not in road_classes:
            continue
        pts = _iter_polyline_points(e, node_lookup)
        segs.extend(_polyline_segments(pts))
    return segs


def _edge_segments_with_class(
    edges: Sequence[object],
    nodes: Sequence[object],
) -> list[tuple[str, Segment]]:
    node_lookup = {str(getattr(n, "id")): Vec2(float(getattr(n, "pos").x), float(getattr(n, "pos").y)) for n in nodes if hasattr(n, "pos")}
    out: list[tuple[str, Segment]] = []
    for e in edges:
        rc = str(getattr(e, "road_class", ""))
        pts = _iter_polyline_points(e, node_lookup)
        for seg in _polyline_segments(pts):
            out.append((rc, seg))
    return out


def _resample_grid_nn(grid: np.ndarray, target_res: int) -> np.ndarray:
    rows, cols = grid.shape
    if rows == target_res and cols == target_res:
        return grid
    ys = np.linspace(0, rows - 1, target_res)
    xs = np.linspace(0, cols - 1, target_res)
    yi = np.clip(np.round(ys).astype(int), 0, rows - 1)
    xi = np.clip(np.round(xs).astype(int), 0, cols - 1)
    return grid[np.ix_(yi, xi)]


def _tensor_add(xx: np.ndarray, xy: np.ndarray, yy: np.ndarray, iy: int, ix: int, d: Vec2, w: float) -> None:
    if w <= 0.0:
        return
    n = d.normalized()
    if n.length() <= 1e-9:
        return
    xx[iy, ix] += w * n.x * n.x
    xy[iy, ix] += w * n.x * n.y
    yy[iy, ix] += w * n.y * n.y


def _segment_tangent(seg: Segment) -> Vec2:
    return seg.vector().normalized()


def _nearest_segment_tangent(point: Vec2, segs: Sequence[Segment]) -> tuple[Optional[Vec2], float]:
    best_tan: Optional[Vec2] = None
    best_dist = float("inf")
    for seg in segs:
        d = point_segment_distance(point, seg)
        if d < best_dist:
            best_dist = d
            best_tan = _segment_tangent(seg)
    return best_tan, float(best_dist)


def _river_boundary_segments(river_areas: Optional[Sequence[object]]) -> list[Segment]:
    if not river_areas:
        return []
    segs: list[Segment] = []
    for area in river_areas:
        pts_raw = getattr(area, "points", None) or []
        pts: list[Vec2] = []
        for p in pts_raw:
            x = p.x if hasattr(p, "x") else p["x"]
            y = p.y if hasattr(p, "y") else p["y"]
            pts.append(Vec2(float(x), float(y)))
        if len(pts) < 3:
            continue
        loop = pts + [pts[0]]
        for i in range(len(loop) - 1):
            seg = Segment(loop[i], loop[i + 1])
            if seg.length() > 1e-6:
                segs.append(seg)
    return segs


def build_tensor_field_grid(
    *,
    extent_m: float,
    height: Optional[np.ndarray],
    river_areas: Optional[Sequence[object]],
    nodes: Sequence[object],
    edges: Sequence[object],
    hubs: Sequence[object],
    cfg: TensorStreamlineConfig,
) -> TensorFieldGrid:
    res = int(max(16, cfg.tensor_grid_resolution))
    xx = np.zeros((res, res), dtype=np.float64)
    xy = np.zeros((res, res), dtype=np.float64)
    yy = np.zeros((res, res), dtype=np.float64)

    river_segs = _river_boundary_segments(river_areas)
    arterial_segs = [seg for rc, seg in _edge_segments_with_class(edges, nodes) if rc == "arterial"]

    slope_norm = None
    contour_tx = None
    contour_ty = None
    if isinstance(height, np.ndarray) and height.ndim == 2 and height.size:
        h = _resample_grid_nn(height.astype(np.float64), res)
        gy, gx = np.gradient(h)
        slope_mag = np.sqrt(gx * gx + gy * gy)
        max_s = float(np.max(slope_mag)) if slope_mag.size else 0.0
        slope_norm = slope_mag / (max_s + 1e-9) if max_s > 0 else np.zeros_like(slope_mag)
        # contour tangent is perpendicular to gradient
        contour_tx = -gy
        contour_ty = gx

    hub_pts: list[Vec2] = []
    for hub in hubs:
        pos = getattr(hub, "pos", None)
        if pos is not None:
            hub_pts.append(Vec2(float(pos.x), float(pos.y)))
            continue
        hx = getattr(hub, "x", None)
        hy = getattr(hub, "y", None)
        if hx is not None and hy is not None:
            hub_pts.append(Vec2(float(hx), float(hy)))

    for iy in range(res):
        wy = (iy / float(max(res - 1, 1))) * float(extent_m)
        for ix in range(res):
            wx = (ix / float(max(res - 1, 1))) * float(extent_m)
            p = Vec2(wx, wy)

            # River tangent field (priority near river)
            if river_segs:
                tan, dist = _nearest_segment_tangent(p, river_segs)
                if tan is not None:
                    w = float(cfg.tensor_water_tangent_weight) * exp(-((dist / max(cfg.tensor_water_influence_m, 1e-6)) ** 2))
                    _tensor_add(xx, xy, yy, iy, ix, tan, w)

            # Contour tangent field (strength proportional to slope)
            if contour_tx is not None and contour_ty is not None and slope_norm is not None:
                tx = float(contour_tx[iy, ix])
                ty = float(contour_ty[iy, ix])
                d = Vec2(tx, ty)
                slope_w = float(slope_norm[iy, ix])
                if d.length() > 1e-9 and slope_w > 0.02:
                    _tensor_add(xx, xy, yy, iy, ix, d, float(cfg.tensor_contour_tangent_weight) * slope_w)

            # Arterial alignment field
            if arterial_segs:
                tan, dist = _nearest_segment_tangent(p, arterial_segs)
                if tan is not None:
                    w = float(cfg.tensor_arterial_align_weight) * exp(-((dist / max(cfg.tensor_arterial_influence_m, 1e-6)) ** 2))
                    _tensor_add(xx, xy, yy, iy, ix, tan, w)

            # Hub attract field (weak, radial)
            if hub_pts:
                nearest = min(hub_pts, key=lambda hp: hp.distance_to(p))
                radial = (nearest - p).normalized()
                if radial.length() > 1e-9:
                    hub_scale = exp(-((nearest.distance_to(p) / max(cfg.tensor_arterial_influence_m * 1.25, 1e-6)) ** 2))
                    _tensor_add(xx, xy, yy, iy, ix, radial, float(cfg.tensor_hub_attract_weight) * hub_scale)

            # Fallback isotropic bias to avoid zero tensors in flat areas
            if xx[iy, ix] + yy[iy, ix] <= 1e-9:
                _tensor_add(xx, xy, yy, iy, ix, Vec2(1.0, 0.0), 1e-3)

    # Anisotropy strength = lambda_max - lambda_min
    strength = np.zeros((res, res), dtype=np.float64)
    for iy in range(res):
        for ix in range(res):
            a = float(xx[iy, ix])
            b = float(xy[iy, ix])
            c = float(yy[iy, ix])
            tr = a + c
            disc = max(0.0, (a - c) * (a - c) + 4.0 * b * b)
            root = disc ** 0.5
            lam1 = 0.5 * (tr + root)
            lam2 = 0.5 * (tr - root)
            strength[iy, ix] = max(0.0, lam1 - lam2)

    return TensorFieldGrid(
        extent_m=float(extent_m),
        resolution=res,
        tensor_xx=xx,
        tensor_xy=xy,
        tensor_yy=yy,
        strength=strength,
    )


def seed_collector_portals(
    *,
    extent_m: float,
    nodes: Sequence[object],
    edges: Sequence[object],
    blocks: Optional[Sequence[object]],
    river_union: object,
    cfg: TensorStreamlineConfig,
    seed: int,
) -> List[Vec2]:
    rng = np.random.default_rng(int(seed) + 8107)
    node_lookup = {str(getattr(n, "id")): Vec2(float(getattr(n, "pos").x), float(getattr(n, "pos").y)) for n in nodes if hasattr(n, "pos")}
    seeds: list[Vec2] = []

    def add_seed(p: Vec2) -> None:
        if not (0.0 <= p.x <= extent_m and 0.0 <= p.y <= extent_m):
            return
        if any(p.distance_to(s) < 0.6 * float(cfg.tensor_seed_spacing_m) for s in seeds):
            return
        seeds.append(p)

    # Portal seeds along arterials.
    spacing = max(20.0, float(cfg.tensor_seed_spacing_m))
    for edge in edges:
        if str(getattr(edge, "road_class", "")) != "arterial":
            continue
        pts = _iter_polyline_points(edge, node_lookup)
        if len(pts) < 2:
            continue
        segs = _polyline_segments(pts)
        if not segs:
            continue
        cum = [0.0]
        total = 0.0
        for seg in segs:
            total += seg.length()
            cum.append(total)
        if total < 40.0:
            continue
        n = max(1, int(total / spacing))
        bridge_bias_skip = bool(int(getattr(edge, "river_crossings", 0)) > 0)
        for i in range(n):
            d = (i + 1) / float(n + 1) * total
            if bridge_bias_skip and abs(d - 0.5 * total) < 0.18 * total:
                continue
            for si, seg in enumerate(segs):
                if cum[si] <= d <= cum[si + 1]:
                    local_t = (d - cum[si]) / max(cum[si + 1] - cum[si], 1e-9)
                    p = seg.point_at(local_t)
                    add_seed(p)
                    break

    # Block centroid seeds for large blocks.
    for block in blocks or []:
        area = float(getattr(block, "area", 0.0) or 0.0)
        if area < max(spacing * spacing * 0.8, 30_000.0):
            continue
        c = getattr(block, "centroid", None)
        if c is not None:
            add_seed(Vec2(float(c.x), float(c.y)))
        if area > max(spacing * spacing * 2.4, 120_000.0):
            rp = getattr(block, "representative_point", None)
            if callable(rp):
                p = rp()
                add_seed(Vec2(float(p.x), float(p.y)))

    # River-parallel offset seeds (heuristic): points near river but outside setback.
    if river_union is not None and not getattr(river_union, "is_empty", True):
        try:
            from shapely.geometry import Point  # type: ignore
            from shapely.ops import nearest_points  # type: ignore
        except Exception:
            pass
        else:
            for block in blocks or []:
                c = getattr(block, "centroid", None)
                if c is None:
                    continue
                cp = Point(float(c.x), float(c.y))
                dist = float(river_union.distance(cp))
                if not (float(cfg.river_setback_m) + 5.0 <= dist <= float(cfg.tensor_water_influence_m)):
                    continue
                n0, n1 = nearest_points(river_union, cp)
                dx = float(cp.x - n0.x)
                dy = float(cp.y - n0.y)
                mag = hypot(dx, dy)
                if mag <= 1e-6:
                    continue
                target = Vec2(
                    float(n0.x + dx / mag * (cfg.river_setback_m + min(0.35 * spacing, 120.0))),
                    float(n0.y + dy / mag * (cfg.river_setback_m + min(0.35 * spacing, 120.0))),
                )
                add_seed(target)
                if rng.random() < 0.35:
                    tang = Vec2(-dy / mag, dx / mag)
                    add_seed(target + tang * float(rng.uniform(0.2, 0.5) * spacing))

    return seeds


def _point_in_river_forbidden(p: Vec2, *, extent_m: float, river_mask: np.ndarray, forbidden_geom: object) -> bool:
    if forbidden_geom is not None and not getattr(forbidden_geom, "is_empty", True):
        try:
            from shapely.geometry import Point  # type: ignore
        except Exception:
            pass
        else:
            return bool(forbidden_geom.contains(Point(float(p.x), float(p.y))))
    if river_mask.size:
        rows, cols = river_mask.shape
        ix = int(round((p.x / max(extent_m, 1e-9)) * (cols - 1))) if cols > 1 else 0
        iy = int(round((p.y / max(extent_m, 1e-9)) * (rows - 1))) if rows > 1 else 0
        ix = min(max(ix, 0), cols - 1)
        iy = min(max(iy, 0), rows - 1)
        return bool(river_mask[iy, ix])
    return False


def _polyline_length(points: Sequence[Vec2]) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        total += points[i].distance_to(points[i + 1])
    return float(total)


def _nearest_road_distance_and_projection(p: Vec2, segments: Sequence[Segment]) -> tuple[float, Optional[Vec2]]:
    best_d = float("inf")
    best_proj = None
    for seg in segments:
        proj = project_point_to_segment(p, seg)
        d = p.distance_to(proj)
        if d < best_d:
            best_d = d
            best_proj = proj
    return float(best_d), best_proj


def trace_streamline(
    *,
    seed: Vec2,
    field: TensorFieldGrid,
    sign: int,
    extent_m: float,
    river_mask: np.ndarray,
    forbidden_geom: object,
    road_segments: Sequence[Segment],
    collector_segments: Sequence[Segment],
    cfg: TensorStreamlineConfig,
) -> TraceResult:
    pts: list[Vec2] = [seed]
    prev_dir: Optional[Vec2] = None
    total_len = 0.0
    low_strength_steps = 0
    max_steps = max(8, int(cfg.tensor_max_trace_len_m / max(cfg.tensor_step_m, 1.0)) + 2)
    junction_probe = max(8.0, cfg.tensor_step_m * 0.9)
    stop_reason = "max_steps"

    for _ in range(max_steps):
        cur = pts[-1]
        d = field.sample_major_dir(cur)
        s = field.sample_strength(cur)
        if s < 1e-4:
            low_strength_steps += 1
        else:
            low_strength_steps = 0
        if low_strength_steps >= 4:
            stop_reason = "low_strength"
            break

        if sign < 0:
            d = Vec2(-d.x, -d.y)
        if prev_dir is not None and d.dot(prev_dir) < 0.0:
            d = Vec2(-d.x, -d.y)
        if prev_dir is not None and prev_dir.length() > 1e-9 and d.length() > 1e-9:
            ang = degrees(np.arccos(max(-1.0, min(1.0, d.normalized().dot(prev_dir.normalized())))))
            if ang > cfg.tensor_turn_limit_deg:
                # Soft clamp by interpolating directions
                w = max(0.0, min(1.0, cfg.tensor_turn_limit_deg / max(ang, 1e-6)))
                d = Vec2(prev_dir.x * (1.0 - w) + d.x * w, prev_dir.y * (1.0 - w) + d.y * w).normalized()
        if d.length() <= 1e-9:
            stop_reason = "zero_dir"
            break

        nxt = Vec2(cur.x + d.x * cfg.tensor_step_m, cur.y + d.y * cfg.tensor_step_m)
        if not (0.0 <= nxt.x <= extent_m and 0.0 <= nxt.y <= extent_m):
            stop_reason = "boundary"
            break
        if _point_in_river_forbidden(nxt, extent_m=extent_m, river_mask=river_mask, forbidden_geom=forbidden_geom):
            stop_reason = "river_forbidden"
            break

        seg = Segment(cur, nxt)
        # Self-intersection check
        if len(pts) >= 4:
            for i in range(len(pts) - 3):
                prior = Segment(pts[i], pts[i + 1])
                hit = segment_intersection(seg, prior)
                if hit.kind in ("point", "overlap"):
                    stop_reason = "self_intersection"
                    nxt = cur
                    break
            if nxt == cur:
                break

        d_existing, proj_existing = _nearest_road_distance_and_projection(nxt, road_segments)
        d_collectors, proj_collectors = _nearest_road_distance_and_projection(nxt, collector_segments) if collector_segments else (float("inf"), None)
        d_near = min(d_existing, d_collectors)
        proj_near = proj_existing if d_existing <= d_collectors else proj_collectors
        if d_near < junction_probe and proj_near is not None and len(pts) >= 1:
            # Stop at projected point to encourage later T-junction formation.
            if proj_near.distance_to(cur) > 2.0:
                pts.append(proj_near)
                total_len += cur.distance_to(proj_near)
            stop_reason = "near_network"
            break

        pts.append(nxt)
        total_len += cur.distance_to(nxt)
        prev_dir = d
        if total_len >= cfg.tensor_max_trace_len_m:
            stop_reason = "max_len"
            break

    if len(pts) >= 2 and pts[-1].distance_to(pts[-2]) < 1e-6:
        pts = pts[:-1]

    conn = 0
    if pts:
        d0, _ = _nearest_road_distance_and_projection(pts[0], road_segments)
        d1, _ = _nearest_road_distance_and_projection(pts[-1], road_segments)
        if d0 < max(20.0, cfg.tensor_step_m * 1.2):
            conn += 1
        if d1 < max(20.0, cfg.tensor_step_m * 1.2):
            conn += 1
    return TraceResult(points=pts, connection_count=conn, reason=stop_reason)


def _merge_bidirectional(seed: Vec2, a: TraceResult, b: TraceResult) -> TraceResult:
    left = list(reversed(a.points)) if a.points else [seed]
    right = b.points if b.points else [seed]
    pts: list[Vec2] = []
    for p in left + right[1:]:
        if not pts or p.distance_to(pts[-1]) > 1e-6:
            pts.append(p)
    return TraceResult(points=pts, connection_count=int(a.connection_count + b.connection_count), reason=f"{a.reason}+{b.reason}")


def generate_tensor_collectors(
    *,
    extent_m: float,
    height: Optional[np.ndarray],
    river_mask: np.ndarray,
    river_areas: Optional[Sequence[object]],
    river_union: object,
    nodes: Sequence[object],
    edges: Sequence[object],
    hubs: Sequence[object],
    blocks: Optional[Sequence[object]],
    cfg: TensorStreamlineConfig,
    seed: int,
) -> tuple[list[list[Vec2]], list[str]]:
    try:
        forbidden_geom = None
        if river_union is not None and not getattr(river_union, "is_empty", True) and cfg.river_setback_m > 0.0:
            forbidden_geom = river_union.buffer(float(cfg.river_setback_m))
    except Exception:
        forbidden_geom = None

    field = build_tensor_field_grid(
        extent_m=extent_m,
        height=height,
        river_areas=river_areas,
        nodes=nodes,
        edges=edges,
        hubs=hubs,
        cfg=cfg,
    )

    seeds = seed_collector_portals(
        extent_m=extent_m,
        nodes=nodes,
        edges=edges,
        blocks=blocks,
        river_union=river_union,
        cfg=cfg,
        seed=seed,
    )

    road_segments = _flatten_segments_from_edges(edges, nodes, road_classes={"arterial", "collector", "local"})
    collector_segments_runtime: list[Segment] = []
    traces: list[list[Vec2]] = []
    notes = [f"tensor_field_res:{field.resolution}", f"tensor_seed_count:{len(seeds)}"]

    for s in seeds:
        if _point_in_river_forbidden(s, extent_m=extent_m, river_mask=river_mask, forbidden_geom=forbidden_geom):
            continue
        # avoid seeding too close to already accepted collectors
        d_seed_existing, _ = _nearest_road_distance_and_projection(s, collector_segments_runtime) if collector_segments_runtime else (float("inf"), None)
        if d_seed_existing < 0.6 * cfg.tensor_seed_spacing_m:
            continue

        neg = trace_streamline(
            seed=s,
            field=field,
            sign=-1,
            extent_m=extent_m,
            river_mask=river_mask,
            forbidden_geom=forbidden_geom,
            road_segments=road_segments,
            collector_segments=collector_segments_runtime,
            cfg=cfg,
        )
        pos = trace_streamline(
            seed=s,
            field=field,
            sign=1,
            extent_m=extent_m,
            river_mask=river_mask,
            forbidden_geom=forbidden_geom,
            road_segments=road_segments,
            collector_segments=collector_segments_runtime,
            cfg=cfg,
        )
        merged = _merge_bidirectional(s, neg, pos)
        if len(merged.points) < 2:
            continue
        if _polyline_length(merged.points) < cfg.tensor_min_trace_len_m:
            continue
        if merged.connection_count < 1:
            continue
        # Prevent purely duplicated corridor
        d_mid, _ = _nearest_road_distance_and_projection(merged.points[len(merged.points) // 2], road_segments + collector_segments_runtime)
        if d_mid < max(6.0, cfg.tensor_step_m * 0.35):
            continue

        traces.append(merged.points)
        collector_segments_runtime.extend(_polyline_segments(merged.points))

    notes.append(f"tensor_trace_count:{len(traces)}")
    return traces, notes

