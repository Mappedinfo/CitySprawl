from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

import numpy as np

from engine.terrain.hydrology import downsample_grid


@dataclass
class AnalysisSurfaces:
    suitability: np.ndarray
    flood_risk: np.ndarray
    river_distance_m: np.ndarray
    height_preview: np.ndarray
    slope_preview: np.ndarray


def _extract_river_points(river_polylines: Sequence[object]) -> np.ndarray:
    pts = []
    for river in river_polylines:
        points = None
        if isinstance(river, dict):
            points = river.get('points')
        else:
            points = getattr(river, 'points', None)
        if not points:
            continue
        for p in points:
            if isinstance(p, dict):
                x = p.get('x')
                y = p.get('y')
            else:
                x = getattr(p, 'x', None)
                y = getattr(p, 'y', None)
            if x is None or y is None:
                continue
            pts.append((float(x), float(y)))
    if not pts:
        return np.zeros((0, 2), dtype=np.float64)
    return np.array(pts, dtype=np.float64)


def _distance_to_rivers_preview(rows: int, cols: int, extent_m: float, river_points: np.ndarray) -> np.ndarray:
    if rows <= 0 or cols <= 0:
        return np.zeros((0, 0), dtype=np.float64)
    if river_points.size == 0:
        return np.full((rows, cols), extent_m, dtype=np.float64)

    xs = np.linspace(0.0, extent_m, cols)
    ys = np.linspace(0.0, extent_m, rows)
    xx, yy = np.meshgrid(xs, ys)
    samples = np.stack([xx.reshape(-1), yy.reshape(-1)], axis=1)

    min_dist = np.full(samples.shape[0], extent_m, dtype=np.float64)
    chunk = 4096
    for start in range(0, samples.shape[0], chunk):
        end = min(samples.shape[0], start + chunk)
        pts = samples[start:end]
        deltas = river_points[None, :, :] - pts[:, None, :]
        dist = np.sqrt(np.sum(deltas * deltas, axis=2))
        min_dist[start:end] = np.min(dist, axis=1)

    return min_dist.reshape(rows, cols)


def _normalize(grid: np.ndarray) -> np.ndarray:
    if grid.size == 0:
        return grid.astype(np.float64)
    g = grid.astype(np.float64)
    g_min = float(np.min(g))
    g_max = float(np.max(g))
    if g_max - g_min < 1e-9:
        return np.zeros_like(g)
    return (g - g_min) / (g_max - g_min)


def compute_suitability_and_flood(
    height: np.ndarray,
    slope: np.ndarray,
    extent_m: float,
    river_polylines: Sequence[object],
    max_resolution: int = 128,
) -> AnalysisSurfaces:
    height_preview = downsample_grid(height, max_resolution=max_resolution)
    slope_preview = downsample_grid(slope, max_resolution=max_resolution)
    rows, cols = height_preview.shape

    river_points = _extract_river_points(river_polylines)
    river_distance = _distance_to_rivers_preview(rows, cols, extent_m, river_points)

    height_norm = _normalize(height_preview)
    slope_norm = _normalize(slope_preview)

    target = max(40.0, extent_m * 0.08)
    sigma = max(60.0, extent_m * 0.12)
    river_access = np.exp(-((river_distance - target) ** 2) / (2.0 * sigma * sigma))
    near_river = np.exp(-(river_distance ** 2) / (2.0 * (max(30.0, extent_m * 0.04) ** 2)))
    lowland = np.clip(1.0 - height_norm, 0.0, 1.0)

    yy, xx = np.meshgrid(np.linspace(-1.0, 1.0, rows), np.linspace(-1.0, 1.0, cols), indexing='ij')
    radial = np.sqrt(xx * xx + yy * yy)
    center_bias = np.clip(1.0 - radial, 0.0, 1.0)

    flood_risk = np.clip(0.65 * near_river + 0.35 * lowland, 0.0, 1.0)
    suitability = (
        0.42 * (1.0 - np.clip(slope_norm, 0.0, 1.0))
        + 0.28 * river_access
        + 0.18 * center_bias
        + 0.12 * (1.0 - np.clip(height_norm, 0.0, 1.0))
        - 0.35 * flood_risk
    )
    suitability = np.clip(suitability, 0.0, 1.0)

    return AnalysisSurfaces(
        suitability=suitability.astype(np.float64),
        flood_risk=flood_risk.astype(np.float64),
        river_distance_m=river_distance.astype(np.float64),
        height_preview=height_preview.astype(np.float64),
        slope_preview=slope_preview.astype(np.float64),
    )
