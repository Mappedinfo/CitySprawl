from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

from engine.models import ContourLine, Point2D
from engine.terrain.hydrology import downsample_grid

# marching squares edges: 0 top, 1 right, 2 bottom, 3 left
_CASE_SEGMENTS = {
    0: [],
    1: [(3, 2)],
    2: [(2, 1)],
    3: [(3, 1)],
    4: [(0, 1)],
    5: [(0, 3), (1, 2)],
    6: [(0, 2)],
    7: [(0, 3)],
    8: [(0, 3)],
    9: [(0, 2)],
    10: [(0, 1), (2, 3)],
    11: [(0, 1)],
    12: [(3, 1)],
    13: [(2, 1)],
    14: [(3, 2)],
    15: [],
}


def _interp(p0: Tuple[float, float], p1: Tuple[float, float], v0: float, v1: float, level: float) -> Tuple[float, float]:
    if abs(v1 - v0) < 1e-9:
        t = 0.5
    else:
        t = (level - v0) / (v1 - v0)
    t = max(0.0, min(1.0, float(t)))
    return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)


def _cell_edge_point(ix: int, iy: int, edge: int, vals: Tuple[float, float, float, float], level: float) -> Tuple[float, float]:
    # vertices: v0 tl, v1 tr, v2 br, v3 bl in cell-local coordinates
    v0, v1, v2, v3 = vals
    if edge == 0:
        return _interp((ix, iy), (ix + 1, iy), v0, v1, level)
    if edge == 1:
        return _interp((ix + 1, iy), (ix + 1, iy + 1), v1, v2, level)
    if edge == 2:
        return _interp((ix + 1, iy + 1), (ix, iy + 1), v2, v3, level)
    return _interp((ix, iy + 1), (ix, iy), v3, v0, level)


def extract_contour_lines(
    height: np.ndarray,
    extent_m: float,
    max_resolution: int = 128,
    contour_count: int = 12,
    max_segments: int = 6000,
) -> List[ContourLine]:
    grid = downsample_grid(height, max_resolution=max_resolution).astype(np.float64)
    rows, cols = grid.shape
    if rows < 2 or cols < 2:
        return []

    gmin = float(np.min(grid))
    gmax = float(np.max(grid))
    if gmax - gmin < 1e-9:
        return []

    levels = np.linspace(gmin, gmax, contour_count + 2)[1:-1]
    out: List[ContourLine] = []
    sx = extent_m / float(max(cols - 1, 1))
    sy = extent_m / float(max(rows - 1, 1))

    for level in levels:
        elev_norm = float((level - gmin) / (gmax - gmin))
        for y in range(rows - 1):
            for x in range(cols - 1):
                v0 = float(grid[y, x])
                v1 = float(grid[y, x + 1])
                v2 = float(grid[y + 1, x + 1])
                v3 = float(grid[y + 1, x])
                case = ((1 if v0 >= level else 0) << 3) | ((1 if v1 >= level else 0) << 2) | ((1 if v2 >= level else 0) << 1) | (1 if v3 >= level else 0)
                segs = _CASE_SEGMENTS.get(case, [])
                if not segs:
                    continue
                vals = (v0, v1, v2, v3)
                for e0, e1 in segs:
                    p0 = _cell_edge_point(x, y, e0, vals, float(level))
                    p1 = _cell_edge_point(x, y, e1, vals, float(level))
                    wp0 = Point2D(x=float(p0[0] * sx), y=float(p0[1] * sy))
                    wp1 = Point2D(x=float(p1[0] * sx), y=float(p1[1] * sy))
                    out.append(ContourLine(id=f'contour-{len(out)}', points=[wp0, wp1], elevation_norm=elev_norm))
                    if len(out) >= max_segments:
                        return out
    return out
