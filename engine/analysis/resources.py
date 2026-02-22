from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np

from engine.models import ResourceSite


def _grid_to_world(ix: int, iy: int, rows: int, cols: int, extent_m: float) -> Tuple[float, float]:
    x = (ix / float(max(cols - 1, 1))) * extent_m
    y = (iy / float(max(rows - 1, 1))) * extent_m
    return (float(x), float(y))


def _far_enough(existing: List[Tuple[float, float]], x: float, y: float, min_dist: float) -> bool:
    for ex, ey in existing:
        if (ex - x) ** 2 + (ey - y) ** 2 < min_dist * min_dist:
            return False
    return True


def _pick_cells_by_score(
    score: np.ndarray,
    count: int,
    extent_m: float,
    min_distance_m: float,
    rng: np.random.Generator,
    jitter_m: float = 0.0,
) -> List[Tuple[float, float, float]]:
    rows, cols = score.shape
    flat = score.reshape(-1)
    order = np.argsort(flat)[::-1]
    picks: List[Tuple[float, float, float]] = []
    existing: List[Tuple[float, float]] = []
    for idx in order:
        if len(picks) >= count:
            break
        s = float(flat[int(idx)])
        if s <= 0.0:
            break
        y, x = divmod(int(idx), cols)
        wx, wy = _grid_to_world(x, y, rows, cols, extent_m)
        if jitter_m > 0.0:
            wx += float(rng.uniform(-jitter_m, jitter_m))
            wy += float(rng.uniform(-jitter_m, jitter_m))
            wx = float(min(max(wx, 0.0), extent_m))
            wy = float(min(max(wy, 0.0), extent_m))
        if not _far_enough(existing, wx, wy, min_distance_m):
            continue
        existing.append((wx, wy))
        picks.append((wx, wy, s))
    return picks


def _river_water_sites(
    river_polylines: Sequence[object],
    limit: int,
    rng: np.random.Generator,
) -> List[Tuple[float, float, float]]:
    points: List[Tuple[float, float, float]] = []
    for river in river_polylines:
        river_points = None
        flow = None
        if isinstance(river, dict):
            river_points = river.get('points', [])
            flow = float(river.get('flow', 1.0))
        else:
            river_points = getattr(river, 'points', [])
            flow = float(getattr(river, 'flow', 1.0))
        if not river_points:
            continue
        step = max(1, len(river_points) // 4)
        for p in river_points[::step]:
            if isinstance(p, dict):
                x = p.get('x')
                y = p.get('y')
            else:
                x = getattr(p, 'x', None)
                y = getattr(p, 'y', None)
            if x is None or y is None:
                continue
            q = min(1.0, np.log10(1.0 + flow) / 4.0)
            points.append((float(x), float(y), float(q)))
    if len(points) <= limit:
        return points
    order = np.argsort([p[2] for p in points])[::-1]
    selected = [points[int(i)] for i in order[:limit * 2]]
    rng.shuffle(selected)
    selected.sort(key=lambda p: p[2], reverse=True)
    return selected[:limit]


def generate_resource_sites(
    seed: int,
    extent_m: float,
    suitability: np.ndarray,
    flood_risk: np.ndarray,
    height_preview: np.ndarray,
    slope_preview: np.ndarray,
    river_polylines: Sequence[object],
) -> List[ResourceSite]:
    rng = np.random.default_rng(seed + 3101)
    rows, cols = suitability.shape
    sites: List[ResourceSite] = []

    height_norm = (height_preview - np.min(height_preview)) / (float(np.max(height_preview) - np.min(height_preview)) + 1e-9)
    slope_norm = (slope_preview - np.min(slope_preview)) / (float(np.max(slope_preview) - np.min(slope_preview)) + 1e-9)

    water_points = _river_water_sites(river_polylines, limit=8, rng=rng)
    for i, (x, y, q) in enumerate(water_points):
        sites.append(
            ResourceSite(
                id=f'res-water-{i}',
                x=x,
                y=y,
                kind='water',
                quality=float(q),
                influence_radius_m=float(max(90.0, extent_m * 0.07)),
            )
        )

    agri_score = np.clip(0.65 * suitability + 0.2 * (1.0 - flood_risk) + 0.15 * (1.0 - slope_norm), 0.0, 1.0)
    for i, (x, y, q) in enumerate(_pick_cells_by_score(agri_score, 6, extent_m, max(80.0, extent_m * 0.08), rng, jitter_m=12.0)):
        sites.append(
            ResourceSite(
                id=f'res-agri-{i}',
                x=x,
                y=y,
                kind='agri',
                quality=float(q),
                influence_radius_m=float(max(120.0, extent_m * 0.09)),
            )
        )

    ore_score = np.clip(0.6 * height_norm + 0.4 * slope_norm, 0.0, 1.0)
    for i, (x, y, q) in enumerate(_pick_cells_by_score(ore_score, 5, extent_m, max(100.0, extent_m * 0.1), rng, jitter_m=10.0)):
        sites.append(
            ResourceSite(
                id=f'res-ore-{i}',
                x=x,
                y=y,
                kind='ore',
                quality=float(q),
                influence_radius_m=float(max(80.0, extent_m * 0.06)),
            )
        )

    forest_score = np.clip(0.4 * (1.0 - suitability) + 0.35 * (1.0 - flood_risk) + 0.25 * (1.0 - np.abs(slope_norm - 0.45) * 2.0), 0.0, 1.0)
    for i, (x, y, q) in enumerate(_pick_cells_by_score(forest_score, 6, extent_m, max(90.0, extent_m * 0.09), rng, jitter_m=15.0)):
        sites.append(
            ResourceSite(
                id=f'res-forest-{i}',
                x=x,
                y=y,
                kind='forest',
                quality=float(q),
                influence_radius_m=float(max(110.0, extent_m * 0.08)),
            )
        )

    return sites
