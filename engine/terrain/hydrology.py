from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import hypot
from typing import Dict, List, Sequence, Tuple

import numpy as np

from engine.core.geometry import Vec2

D8_OFFSETS: Sequence[Tuple[int, int]] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


@dataclass
class HydrologyResult:
    enabled: bool
    downstream_flat: np.ndarray
    accumulation: np.ndarray
    river_mask: np.ndarray
    river_polylines: List[Dict[str, object]]


def _cell_to_world(ix: int, iy: int, extent_m: float, resolution: int) -> Vec2:
    if resolution <= 1:
        return Vec2(0.0, 0.0)
    x = (ix / float(resolution - 1)) * extent_m
    y = (iy / float(resolution - 1)) * extent_m
    return Vec2(x, y)


def compute_flow_direction(height: np.ndarray) -> np.ndarray:
    rows, cols = height.shape
    downstream = np.arange(rows * cols, dtype=np.int64)

    for y in range(rows):
        for x in range(cols):
            current = height[y, x]
            best_drop = 0.0
            best_idx = y * cols + x
            for dy, dx in D8_OFFSETS:
                ny = y + dy
                nx = x + dx
                if ny < 0 or ny >= rows or nx < 0 or nx >= cols:
                    continue
                drop = current - height[ny, nx]
                if drop > best_drop:
                    best_drop = float(drop)
                    best_idx = ny * cols + nx
            downstream[y * cols + x] = best_idx

    return downstream


def compute_flow_accumulation(height: np.ndarray, downstream_flat: np.ndarray) -> np.ndarray:
    flat_h = height.reshape(-1)
    order = np.argsort(flat_h)[::-1]
    acc = np.ones_like(flat_h, dtype=np.float64)

    for idx in order:
        ds = int(downstream_flat[idx])
        if ds != int(idx):
            acc[ds] += acc[idx]

    return acc.reshape(height.shape)


def _trace_river_chains(
    river_mask: np.ndarray,
    downstream_flat: np.ndarray,
    accumulation: np.ndarray,
    extent_m: float,
    min_river_length_m: float,
) -> List[Dict[str, object]]:
    rows, cols = river_mask.shape
    river_cells = set(int(i) for i in np.flatnonzero(river_mask.reshape(-1)))
    if not river_cells:
        return []

    upstream_counts: Dict[int, int] = defaultdict(int)
    for idx in river_cells:
        ds = int(downstream_flat[idx])
        if ds in river_cells and ds != idx:
            upstream_counts[ds] += 1

    starts = [idx for idx in river_cells if upstream_counts.get(idx, 0) == 0]
    visited = set()
    chains: List[Dict[str, object]] = []

    def walk(start_idx: int) -> List[int]:
        chain: List[int] = []
        idx = start_idx
        local_seen = set()
        while idx in river_cells and idx not in local_seen:
            chain.append(idx)
            local_seen.add(idx)
            visited.add(idx)
            ds = int(downstream_flat[idx])
            if ds == idx or ds not in river_cells:
                break
            idx = ds
        return chain

    for start in starts:
        if start in visited:
            continue
        chain = walk(start)
        if len(chain) < 2:
            continue
        points: List[Vec2] = []
        for flat_idx in chain:
            y, x = divmod(flat_idx, cols)
            points.append(_cell_to_world(x, y, extent_m, rows))

        # Simple smoothing for visualization.
        smoothed: List[Vec2] = []
        for i, pt in enumerate(points):
            x_vals = [pt.x]
            y_vals = [pt.y]
            if i > 0:
                x_vals.append(points[i - 1].x)
                y_vals.append(points[i - 1].y)
            if i + 1 < len(points):
                x_vals.append(points[i + 1].x)
                y_vals.append(points[i + 1].y)
            smoothed.append(Vec2(sum(x_vals) / len(x_vals), sum(y_vals) / len(y_vals)))

        length_m = 0.0
        for i in range(len(smoothed) - 1):
            length_m += hypot(smoothed[i + 1].x - smoothed[i].x, smoothed[i + 1].y - smoothed[i].y)
        if length_m < min_river_length_m:
            continue

        head_acc = float(accumulation.reshape(-1)[chain[0]])
        chains.append(
            {
                "id": f"river-{len(chains)}",
                "points": smoothed,
                "flow": head_acc,
                "length_m": length_m,
            }
        )

    # Capture any isolated cycles or leftovers.
    for leftover in list(river_cells - visited):
        chain = walk(leftover)
        if len(chain) >= 2:
            points = []
            for flat_idx in chain:
                y, x = divmod(flat_idx, cols)
                points.append(_cell_to_world(x, y, extent_m, rows))
            length_m = 0.0
            for i in range(len(points) - 1):
                length_m += hypot(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y)
            if length_m >= min_river_length_m:
                chains.append(
                    {
                        "id": f"river-{len(chains)}",
                        "points": points,
                        "flow": float(accumulation.reshape(-1)[chain[0]]),
                        "length_m": length_m,
                    }
                )

    return chains


def compute_hydrology(
    height: np.ndarray,
    extent_m: float,
    enabled: bool,
    accum_threshold: float,
    min_river_length_m: float,
) -> HydrologyResult:
    downstream_flat = compute_flow_direction(height)
    accumulation = compute_flow_accumulation(height, downstream_flat)

    if not enabled:
        river_mask = np.zeros_like(height, dtype=bool)
        return HydrologyResult(
            enabled=False,
            downstream_flat=downstream_flat,
            accumulation=accumulation,
            river_mask=river_mask,
            river_polylines=[],
        )

    max_acc = float(np.max(accumulation)) if accumulation.size else 0.0
    threshold_value = max_acc * accum_threshold
    river_mask = accumulation >= threshold_value

    polylines = _trace_river_chains(
        river_mask=river_mask,
        downstream_flat=downstream_flat,
        accumulation=accumulation,
        extent_m=extent_m,
        min_river_length_m=min_river_length_m,
    )

    return HydrologyResult(
        enabled=True,
        downstream_flat=downstream_flat,
        accumulation=accumulation,
        river_mask=river_mask,
        river_polylines=polylines,
    )


def downsample_grid(grid: np.ndarray, max_resolution: int = 128) -> np.ndarray:
    if grid.ndim != 2:
        raise ValueError("grid must be 2D")
    rows, cols = grid.shape
    stride = max(1, int(np.ceil(max(rows, cols) / float(max_resolution))))
    return grid[::stride, ::stride]
