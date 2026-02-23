from __future__ import annotations

from dataclasses import dataclass
from math import atan, degrees
from typing import Optional, Sequence

import numpy as np

from engine.core.geometry import Segment, Vec2, point_segment_distance, project_point_to_segment


def _world_to_grid(pos: Vec2, extent_m: float, shape: tuple[int, int]) -> tuple[int, int]:
    rows, cols = shape
    if rows <= 1 or cols <= 1:
        return (0, 0)
    x = int(round((float(pos.x) / max(float(extent_m), 1e-9)) * (cols - 1)))
    y = int(round((float(pos.y) / max(float(extent_m), 1e-9)) * (rows - 1)))
    return (min(max(x, 0), cols - 1), min(max(y, 0), rows - 1))


def _segment_tangent(seg: Segment) -> Vec2:
    return seg.vector().normalized()


def _river_boundary_segments(river_areas: Optional[Sequence[object]]) -> list[Segment]:
    if not river_areas:
        return []
    out: list[Segment] = []
    for area in river_areas:
        if isinstance(area, dict):
            pts_raw = area.get("points", []) or []
        else:
            pts_raw = getattr(area, "points", None) or []
        pts: list[Vec2] = []
        for p in pts_raw:
            if isinstance(p, dict):
                x = p["x"]
                y = p["y"]
            else:
                x = p.x if hasattr(p, "x") else p["x"]
                y = p.y if hasattr(p, "y") else p["y"]
            pts.append(Vec2(float(x), float(y)))
        if len(pts) < 3:
            continue
        loop = pts + [pts[0]]
        for i in range(len(loop) - 1):
            seg = Segment(loop[i], loop[i + 1])
            if seg.length() > 1e-6:
                out.append(seg)
    return out


@dataclass
class TerrainProbeConfig:
    slope_straight_threshold_deg: float = 5.0
    slope_serpentine_threshold_deg: float = 15.0
    slope_hard_limit_deg: float = 22.0
    contour_follow_weight: float = 0.9
    river_snap_dist_m: float = 28.0
    river_parallel_bias_weight: float = 1.0
    river_avoid_weight: float = 1.2
    river_setback_m: float = 18.0


class TerrainProbe:
    def __init__(
        self,
        *,
        extent_m: float,
        height: Optional[np.ndarray],
        slope: np.ndarray,
        river_mask: np.ndarray,
        river_areas: Optional[Sequence[object]],
        river_union: object = None,
        cfg: TerrainProbeConfig | None = None,
    ) -> None:
        self.extent_m = float(extent_m)
        self.height = height if isinstance(height, np.ndarray) and height.ndim == 2 and height.size else None
        self.slope = slope if isinstance(slope, np.ndarray) and slope.ndim == 2 and slope.size else np.zeros((1, 1), dtype=np.float64)
        self.river_mask = river_mask if isinstance(river_mask, np.ndarray) and river_mask.ndim == 2 else np.zeros((1, 1), dtype=bool)
        self.river_union = river_union
        self.cfg = cfg or TerrainProbeConfig()
        self._river_segs = _river_boundary_segments(river_areas)
        self._river_forbidden_geom = None
        if self.river_union is not None and not getattr(self.river_union, "is_empty", True):
            try:
                self._river_forbidden_geom = self.river_union.buffer(float(max(0.0, self.cfg.river_setback_m)))
            except Exception:
                self._river_forbidden_geom = None

        self._gx: Optional[np.ndarray] = None
        self._gy: Optional[np.ndarray] = None
        if self.height is not None:
            rows, cols = self.height.shape
            cell_x = self.extent_m / float(max(cols - 1, 1))
            cell_y = self.extent_m / float(max(rows - 1, 1))
            gy, gx = np.gradient(self.height.astype(np.float64), cell_y, cell_x)
            self._gx = gx
            self._gy = gy

    def sample_slope_value(self, point: Vec2) -> float:
        ix, iy = _world_to_grid(point, self.extent_m, self.slope.shape)
        return float(self.slope[iy, ix])

    def sample_slope_deg(self, point: Vec2) -> float:
        return float(degrees(atan(max(0.0, self.sample_slope_value(point)))))

    def sample_gradient_dir(self, point: Vec2) -> Vec2:
        if self._gx is None or self._gy is None:
            return Vec2(0.0, 0.0)
        ix, iy = _world_to_grid(point, self.extent_m, self._gx.shape)
        return Vec2(float(self._gx[iy, ix]), float(self._gy[iy, ix])).normalized()

    def sample_contour_dir(self, point: Vec2) -> Vec2:
        g = self.sample_gradient_dir(point)
        if g.length() <= 1e-9:
            return Vec2(0.0, 0.0)
        return Vec2(-g.y, g.x).normalized()

    def check_water_hit(self, point: Vec2) -> bool:
        if self._point_in_river_geom(point):
            return True
        if self.river_mask.size:
            ix, iy = _world_to_grid(point, self.extent_m, self.river_mask.shape)
            return bool(self.river_mask[iy, ix])
        return False

    def _point_in_river_geom(self, point: Vec2) -> bool:
        geom = self._river_forbidden_geom if self._river_forbidden_geom is not None else self.river_union
        if geom is None or getattr(geom, "is_empty", True):
            return False
        try:
            from shapely.geometry import Point  # type: ignore
        except Exception:
            return False
        try:
            return bool(geom.contains(Point(float(point.x), float(point.y))))
        except Exception:
            return False

    def nearest_river_bank_tangent(self, point: Vec2) -> tuple[Optional[Vec2], float]:
        best: Optional[Vec2] = None
        best_d = float("inf")
        for seg in self._river_segs:
            d = point_segment_distance(point, seg)
            if d < best_d:
                best_d = d
                best = _segment_tangent(seg)
        return best, float(best_d)

    def nearest_river_bank_projection(self, point: Vec2) -> tuple[Optional[Vec2], float]:
        best_proj: Optional[Vec2] = None
        best_d = float("inf")
        for seg in self._river_segs:
            pproj = project_point_to_segment(point, seg)
            d = point.distance_to(pproj)
            if d < best_d:
                best_d = d
                best_proj = pproj
        return best_proj, float(best_d)

    def snap_or_bias_to_riverfront(self, point: Vec2, direction: Vec2) -> Vec2:
        tan, dist = self.nearest_river_bank_tangent(point)
        if tan is None:
            return direction.normalized()
        if dist > max(float(self.cfg.river_snap_dist_m), 1.0) * 3.0:
            return direction.normalized()
        if tan.dot(direction) < 0.0:
            tan = Vec2(-tan.x, -tan.y)
        w = min(1.0, max(0.0, float(self.cfg.river_parallel_bias_weight)) * (1.0 - dist / max(self.cfg.river_snap_dist_m * 3.0, 1e-6)))
        d = Vec2(direction.x * (1.0 - w) + tan.x * w, direction.y * (1.0 - w) + tan.y * w)
        return d.normalized() if d.length() > 1e-9 else tan

    def _blend_dirs(self, a: Vec2, b: Vec2, w_b: float) -> Vec2:
        if a.length() <= 1e-9:
            return b.normalized()
        if b.length() <= 1e-9:
            return a.normalized()
        if a.dot(b) < 0.0:
            b = Vec2(-b.x, -b.y)
        w_b = max(0.0, min(1.0, float(w_b)))
        d = Vec2(a.x * (1.0 - w_b) + b.x * w_b, a.y * (1.0 - w_b) + b.y * w_b)
        return d.normalized() if d.length() > 1e-9 else a.normalized()

    def adjust_direction_for_slope(self, point: Vec2, current_dir: Vec2, road_class: str = "collector") -> Vec2:
        _ = road_class
        d = current_dir.normalized()
        if d.length() <= 1e-9:
            return d
        slope_deg = self.sample_slope_deg(point)
        if slope_deg < float(self.cfg.slope_straight_threshold_deg):
            return d
        contour = self.sample_contour_dir(point)
        if contour.length() <= 1e-9:
            return d
        # Bias increases with slope; strong bias in serpentine regime.
        if slope_deg >= float(self.cfg.slope_serpentine_threshold_deg):
            w = min(1.0, 0.65 + 0.35 * float(self.cfg.contour_follow_weight))
        else:
            span = max(float(self.cfg.slope_serpentine_threshold_deg - self.cfg.slope_straight_threshold_deg), 1e-6)
            t = (slope_deg - float(self.cfg.slope_straight_threshold_deg)) / span
            w = min(0.85, max(0.1, t * float(self.cfg.contour_follow_weight)))
        return self._blend_dirs(d, contour, w)

    def slope_penalty_for_step(self, start: Vec2, direction: Vec2, step_m: float) -> float:
        end = Vec2(start.x + direction.x * step_m, start.y + direction.y * step_m)
        s_end = self.sample_slope_deg(end)
        # Penalize excessive slope strongly.
        excess = max(0.0, s_end - float(self.cfg.slope_straight_threshold_deg))
        hard_excess = max(0.0, s_end - float(self.cfg.slope_hard_limit_deg))
        return float(excess + hard_excess * 4.0)

    def choose_serpentine_direction(
        self,
        point: Vec2,
        current_dir: Vec2,
        step_m: float,
        *,
        rng: np.random.Generator,
    ) -> Vec2:
        base = self.adjust_direction_for_slope(point, current_dir, road_class="collector")
        if base.length() <= 1e-9:
            base = current_dir.normalized()
        candidates: list[Vec2] = []
        # Search local heading perturbations around slope-adjusted direction.
        for deg_delta in (-70, -50, -30, -15, 0, 15, 30, 50, 70):
            ang = np.deg2rad(float(deg_delta))
            ca = float(np.cos(ang))
            sa = float(np.sin(ang))
            d = Vec2(base.x * ca - base.y * sa, base.x * sa + base.y * ca).normalized()
            if d.length() > 1e-9:
                candidates.append(d)
        if not candidates:
            return base
        best = base
        best_score = float("inf")
        for d in candidates:
            nxt = Vec2(point.x + d.x * step_m, point.y + d.y * step_m)
            if not (0.0 <= nxt.x <= self.extent_m and 0.0 <= nxt.y <= self.extent_m):
                continue
            score = self.slope_penalty_for_step(point, d, step_m)
            if self.check_water_hit(nxt):
                score += 1000.0 * float(self.cfg.river_avoid_weight)
            # Prefer continuity over sharp reversals.
            score += max(0.0, 1.0 - d.dot(current_dir.normalized())) * 8.0
            score += float(rng.uniform(0.0, 0.35))
            if score < best_score:
                best_score = score
                best = d
        return best.normalized()
