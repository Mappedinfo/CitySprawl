from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from engine.terrain.hydrology import downsample_grid


@dataclass
class TerrainVisualSurfaces:
    terrain_class_preview: np.ndarray  # int grid
    hillshade_preview: np.ndarray  # [0,1]
    height_preview: np.ndarray
    slope_preview: np.ndarray


def _normalize(grid: np.ndarray) -> np.ndarray:
    if grid.size == 0:
        return grid.astype(np.float64)
    g = grid.astype(np.float64)
    gmin = float(np.min(g))
    gmax = float(np.max(g))
    if gmax - gmin < 1e-9:
        return np.zeros_like(g)
    return (g - gmin) / (gmax - gmin)


def _extract_river_points(river_polylines: Sequence[object]) -> np.ndarray:
    pts = []
    for river in river_polylines:
        points = river.get('points', []) if isinstance(river, dict) else getattr(river, 'points', [])
        for p in points:
            x = p.get('x') if isinstance(p, dict) else getattr(p, 'x', None)
            y = p.get('y') if isinstance(p, dict) else getattr(p, 'y', None)
            if x is None or y is None:
                continue
            pts.append((float(x), float(y)))
    if not pts:
        return np.zeros((0, 2), dtype=np.float64)
    return np.array(pts, dtype=np.float64)


def _river_distance_grid(rows: int, cols: int, extent_m: float, river_points: np.ndarray) -> np.ndarray:
    if rows == 0 or cols == 0:
        return np.zeros((rows, cols), dtype=np.float64)
    if river_points.size == 0:
        return np.full((rows, cols), extent_m, dtype=np.float64)
    xs = np.linspace(0.0, extent_m, cols)
    ys = np.linspace(0.0, extent_m, rows)
    xx, yy = np.meshgrid(xs, ys)
    samples = np.stack([xx.reshape(-1), yy.reshape(-1)], axis=1)
    out = np.full(samples.shape[0], extent_m, dtype=np.float64)
    chunk = 4096
    for start in range(0, samples.shape[0], chunk):
        end = min(samples.shape[0], start + chunk)
        pts = samples[start:end]
        d = river_points[None, :, :] - pts[:, None, :]
        dist = np.sqrt(np.sum(d * d, axis=2))
        out[start:end] = np.min(dist, axis=1)
    return out.reshape(rows, cols)


def _hillshade(height: np.ndarray, extent_m: float) -> np.ndarray:
    rows, cols = height.shape
    if rows == 0 or cols == 0:
        return np.zeros_like(height, dtype=np.float64)
    cell = extent_m / float(max(rows - 1, 1))
    gy, gx = np.gradient(height.astype(np.float64), cell, cell)
    slope = np.pi / 2.0 - np.arctan(np.sqrt(gx * gx + gy * gy))
    aspect = np.arctan2(-gx, gy)
    az = np.deg2rad(315.0)
    alt = np.deg2rad(45.0)
    shaded = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    shaded = np.clip(shaded, -1.0, 1.0)
    return (shaded + 1.0) / 2.0


def compute_terrain_classification(
    height: np.ndarray,
    slope: np.ndarray,
    extent_m: float,
    river_polylines: Sequence[object],
    max_resolution: int = 128,
) -> TerrainVisualSurfaces:
    height_preview = downsample_grid(height, max_resolution=max_resolution).astype(np.float64)
    slope_preview = downsample_grid(slope, max_resolution=max_resolution).astype(np.float64)
    rows, cols = height_preview.shape

    h = _normalize(height_preview)
    s = _normalize(slope_preview)
    river_pts = _extract_river_points(river_polylines)
    river_dist = _river_distance_grid(rows, cols, extent_m, river_pts)
    near_river = river_dist < max(55.0, extent_m * 0.04)

    terrain_class = np.full((rows, cols), 1, dtype=np.int64)  # plain default
    floodplain_mask = near_river & (h < 0.48)
    terrain_class[floodplain_mask] = 0
    rolling_mask = (s >= 0.18) & (s < 0.36)
    terrain_class[rolling_mask] = 2
    high_hill_mask = ((s >= 0.36) & (s < 0.58)) | ((h > 0.62) & (s > 0.22))
    terrain_class[high_hill_mask] = 3
    mountain_mask = (s >= 0.58) | ((h > 0.78) & (s > 0.34))
    terrain_class[mountain_mask] = 4
    ridge_mask = (h > 0.9) & (s > 0.55)
    terrain_class[ridge_mask] = 5
    terrain_class[floodplain_mask] = 0  # enforce river floodplain precedence

    shade = _hillshade(height_preview, extent_m)
    return TerrainVisualSurfaces(
        terrain_class_preview=terrain_class,
        hillshade_preview=shade.astype(np.float64),
        height_preview=height_preview,
        slope_preview=slope_preview,
    )
