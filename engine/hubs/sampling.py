from __future__ import annotations

from dataclasses import dataclass
from math import ceil, cos, pi, sin, sqrt
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from engine.core.geometry import Vec2


@dataclass
class HubPoint:
    id: str
    pos: Vec2
    tier: int
    score: float
    attrs: Dict[str, float]


@dataclass
class HubPlacementResult:
    hubs: List[HubPoint]
    suitability_preview: np.ndarray
    candidates_sampled: int


@dataclass
class _Candidate:
    pos: Vec2
    score: float
    attrs: Dict[str, float]


def _poisson_disk_points(
    extent_m: float,
    min_distance_m: float,
    rng: np.random.Generator,
    max_points: int,
    k: int = 30,
) -> List[Vec2]:
    if max_points <= 0:
        return []
    min_distance_m = max(1.0, min_distance_m)
    cell = min_distance_m / sqrt(2.0)
    grid_w = int(ceil(extent_m / cell))
    grid_h = int(ceil(extent_m / cell))
    grid: List[List[Optional[Vec2]]] = [[None for _ in range(grid_w)] for _ in range(grid_h)]
    points: List[Vec2] = []
    active: List[Vec2] = []

    def grid_coords(p: Vec2) -> Tuple[int, int]:
        return (min(grid_w - 1, max(0, int(p.x / cell))), min(grid_h - 1, max(0, int(p.y / cell))))

    def in_bounds(p: Vec2) -> bool:
        return 0.0 <= p.x <= extent_m and 0.0 <= p.y <= extent_m

    def fits(p: Vec2) -> bool:
        gx, gy = grid_coords(p)
        for yy in range(max(0, gy - 2), min(grid_h, gy + 3)):
            for xx in range(max(0, gx - 2), min(grid_w, gx + 3)):
                q = grid[yy][xx]
                if q is None:
                    continue
                dx = q.x - p.x
                dy = q.y - p.y
                if dx * dx + dy * dy < min_distance_m * min_distance_m:
                    return False
        return True

    first = Vec2(float(rng.uniform(0.0, extent_m)), float(rng.uniform(0.0, extent_m)))
    points.append(first)
    active.append(first)
    gx, gy = grid_coords(first)
    grid[gy][gx] = first

    while active and len(points) < max_points:
        idx = int(rng.integers(0, len(active)))
        center = active[idx]
        found = False
        for _ in range(k):
            angle = float(rng.uniform(0.0, 2.0 * pi))
            radius = float(rng.uniform(min_distance_m, 2.0 * min_distance_m))
            p = Vec2(center.x + cos(angle) * radius, center.y + sin(angle) * radius)
            if not in_bounds(p):
                continue
            if not fits(p):
                continue
            points.append(p)
            active.append(p)
            gx, gy = grid_coords(p)
            grid[gy][gx] = p
            found = True
            if len(points) >= max_points:
                break
        if not found:
            active.pop(idx)

    return points


def _world_to_grid(pos: Vec2, extent_m: float, resolution: int) -> Tuple[int, int]:
    if resolution <= 1:
        return (0, 0)
    x = int(round((pos.x / extent_m) * (resolution - 1)))
    y = int(round((pos.y / extent_m) * (resolution - 1)))
    x = max(0, min(resolution - 1, x))
    y = max(0, min(resolution - 1, y))
    return x, y


def _collect_river_points(river_polylines: Sequence[Dict[str, object]]) -> np.ndarray:
    pts: List[Tuple[float, float]] = []
    for river in river_polylines:
        for p in river.get("points", []):
            # points are Vec2 in terrain hydrology internal format
            if hasattr(p, "x") and hasattr(p, "y"):
                pts.append((float(p.x), float(p.y)))
    if not pts:
        return np.zeros((0, 2), dtype=np.float64)
    return np.array(pts, dtype=np.float64)


def _score_point(
    pos: Vec2,
    extent_m: float,
    slope: np.ndarray,
    river_points: np.ndarray,
    rng: np.random.Generator,
) -> _Candidate:
    res = slope.shape[0]
    gx, gy = _world_to_grid(pos, extent_m, res)
    slope_val = float(slope[gy, gx])
    slope_norm = slope_val / (float(np.max(slope)) + 1e-9)
    slope_score = 1.0 - min(1.0, slope_norm)

    center = Vec2(extent_m * 0.5, extent_m * 0.5)
    d_center = pos.distance_to(center)
    center_norm = d_center / max(extent_m * 0.75, 1e-6)
    center_score = max(0.0, 1.0 - center_norm)

    river_distance = extent_m
    river_score = 0.35
    flood_penalty = 0.0
    if river_points.size:
        delta = river_points - np.array([[pos.x, pos.y]], dtype=np.float64)
        dists = np.sqrt(np.sum(delta * delta, axis=1))
        river_distance = float(np.min(dists))
        target = max(40.0, extent_m * 0.08)
        sigma = max(60.0, extent_m * 0.12)
        river_score = float(np.exp(-((river_distance - target) ** 2) / (2.0 * sigma * sigma)))
        if river_distance < 25.0:
            flood_penalty = 0.5

    jitter = float(rng.uniform(-0.02, 0.02))
    score = 0.45 * slope_score + 0.35 * river_score + 0.20 * center_score - flood_penalty + jitter
    attrs = {
        "slope_score": slope_score,
        "river_score": river_score,
        "center_score": center_score,
        "river_distance_m": river_distance,
    }
    return _Candidate(pos=pos, score=score, attrs=attrs)


def _make_suitability_preview(
    extent_m: float,
    slope: np.ndarray,
    river_points: np.ndarray,
) -> np.ndarray:
    res = min(128, slope.shape[0])
    stride = max(1, int(np.ceil(slope.shape[0] / float(res))))
    slope_ds = slope[::stride, ::stride]
    rows, cols = slope_ds.shape
    slope_norm = slope_ds / (float(np.max(slope_ds)) + 1e-9)
    base = 1.0 - np.clip(slope_norm, 0.0, 1.0)

    if river_points.size == 0:
        return np.clip(base, 0.0, 1.0)

    # Approximate river-distance reward on downsampled grid.
    xs = np.linspace(0.0, extent_m, cols)
    ys = np.linspace(0.0, extent_m, rows)
    xx, yy = np.meshgrid(xs, ys)
    samples = np.stack([xx.reshape(-1), yy.reshape(-1)], axis=1)
    # Chunk to avoid large temporary arrays.
    min_dist = np.full(samples.shape[0], extent_m, dtype=np.float64)
    chunk = 2048
    for start in range(0, samples.shape[0], chunk):
        end = min(samples.shape[0], start + chunk)
        pts = samples[start:end]
        deltas = river_points[None, :, :] - pts[:, None, :]
        dist = np.sqrt(np.sum(deltas * deltas, axis=2))
        min_dist[start:end] = np.min(dist, axis=1)
    target = max(40.0, extent_m * 0.08)
    sigma = max(60.0, extent_m * 0.12)
    river_reward = np.exp(-((min_dist - target) ** 2) / (2.0 * sigma * sigma)).reshape(rows, cols)
    return np.clip(0.6 * base + 0.4 * river_reward, 0.0, 1.0)


def generate_hubs(
    seed: int,
    extent_m: float,
    slope: np.ndarray,
    river_polylines: Sequence[Dict[str, object]],
    t1_count: int,
    t2_count: int,
    t3_count: int,
    min_distance_m: float,
) -> HubPlacementResult:
    total = t1_count + t2_count + t3_count
    if total <= 0:
        return HubPlacementResult(hubs=[], suitability_preview=np.zeros((1, 1)), candidates_sampled=0)

    rng = np.random.default_rng(seed + 1001)
    # Over-sample candidates then score/select.
    target_candidates = max(total * 5, total + 16)
    candidates = _poisson_disk_points(
        extent_m=extent_m,
        min_distance_m=min_distance_m,
        rng=rng,
        max_points=target_candidates,
    )

    river_points = _collect_river_points(river_polylines)
    scored: List[_Candidate] = [_score_point(pos, extent_m, slope, river_points, rng) for pos in candidates]
    scored.sort(key=lambda c: c.score, reverse=True)

    selected: List[_Candidate] = scored[:total]
    if len(selected) < total:
        # Fallback: random points if Poisson under-fills.
        for _ in range(total - len(selected)):
            p = Vec2(float(rng.uniform(0.0, extent_m)), float(rng.uniform(0.0, extent_m)))
            selected.append(_score_point(p, extent_m, slope, river_points, rng))

    hubs: List[HubPoint] = []
    index = 0
    for tier, count in ((1, t1_count), (2, t2_count), (3, t3_count)):
        for _ in range(count):
            cand = selected[index]
            hubs.append(
                HubPoint(
                    id=f"hub-{index}",
                    pos=cand.pos,
                    tier=tier,
                    score=float(cand.score),
                    attrs=cand.attrs,
                )
            )
            index += 1

    suitability = _make_suitability_preview(extent_m, slope, river_points)
    return HubPlacementResult(hubs=hubs, suitability_preview=suitability, candidates_sampled=len(candidates))
