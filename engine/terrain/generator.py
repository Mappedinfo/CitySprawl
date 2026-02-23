from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import numpy as np

from .hydrology import HydrologyResult, compute_hydrology


@dataclass
class TerrainBundle:
    height: np.ndarray
    slope: np.ndarray
    hydrology: HydrologyResult
    debug: Dict[str, np.ndarray]


def _value_noise_2d(resolution: int, frequency: int, rng: np.random.Generator) -> np.ndarray:
    frequency = max(1, int(frequency))
    lattice = rng.random((frequency + 1, frequency + 1), dtype=np.float64)

    xs = np.linspace(0.0, float(frequency), resolution, endpoint=False)
    ys = np.linspace(0.0, float(frequency), resolution, endpoint=False)

    xi = np.floor(xs).astype(int)
    yi = np.floor(ys).astype(int)
    xf = xs - xi
    yf = ys - yi

    x0 = xi
    x1 = np.clip(xi + 1, 0, frequency)
    y0 = yi
    y1 = np.clip(yi + 1, 0, frequency)

    v00 = lattice[y0[:, None], x0[None, :]]
    v10 = lattice[y0[:, None], x1[None, :]]
    v01 = lattice[y1[:, None], x0[None, :]]
    v11 = lattice[y1[:, None], x1[None, :]]

    sx = xf[None, :]
    sy = yf[:, None]

    i0 = v00 * (1.0 - sx) + v10 * sx
    i1 = v01 * (1.0 - sx) + v11 * sx
    return i0 * (1.0 - sy) + i1 * sy


def _fbm_heightmap(
    resolution: int,
    octaves: int,
    seed: int,
    relief_strength: float,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    height = np.zeros((resolution, resolution), dtype=np.float64)
    amplitude = 1.0
    total_amp = 0.0
    base_freq = 2

    for octave in range(octaves):
        frequency = base_freq * (2 ** octave)
        noise = _value_noise_2d(resolution, frequency, rng)
        height += amplitude * noise
        total_amp += amplitude
        amplitude *= 0.5

    if total_amp > 0:
        height /= total_amp

    # Low-frequency landmass mask to avoid overly uniform terrain.
    grid = np.linspace(-1.0, 1.0, resolution)
    xx, yy = np.meshgrid(grid, grid)
    radial = np.sqrt(xx * xx + yy * yy)
    landmask = np.clip(1.15 - radial ** 1.35, 0.0, 1.0)

    height = 0.72 * height + 0.28 * landmask
    h_min = float(np.min(height))
    h_max = float(np.max(height))
    if h_max - h_min < 1e-9:
        normalized = np.zeros_like(height)
    else:
        normalized = (height - h_min) / (h_max - h_min)
    return np.clip(normalized * relief_strength, 0.0, None)


def compute_slope(height: np.ndarray, extent_m: float) -> np.ndarray:
    res = height.shape[0]
    cell_size = extent_m / float(max(res - 1, 1))
    gy, gx = np.gradient(height, cell_size, cell_size)
    return np.sqrt(gx * gx + gy * gy)


def generate_heightmap(
    resolution: int,
    octaves: int,
    seed: int,
    relief_strength: float,
) -> np.ndarray:
    return _fbm_heightmap(
        resolution=resolution,
        octaves=octaves,
        seed=seed,
        relief_strength=relief_strength,
    )


def generate_terrain_bundle(
    resolution: int,
    extent_m: float,
    octaves: int,
    seed: int,
    relief_strength: float,
    hydrology_enabled: bool,
    accum_threshold: float,
    min_river_length_m: float,
    stream_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> TerrainBundle:
    height = generate_heightmap(resolution, octaves, seed, relief_strength)
    if stream_cb:
        try:
            stream_cb({"event_type": "terrain_milestone", "data": {"stage": "heightmap", "resolution": resolution, "extent_m": extent_m}})
        except Exception:
            pass
    slope = compute_slope(height, extent_m)
    if stream_cb:
        try:
            stream_cb({"event_type": "terrain_milestone", "data": {"stage": "slope"}})
        except Exception:
            pass
    hydrology = compute_hydrology(
        height=height,
        extent_m=extent_m,
        enabled=hydrology_enabled,
        accum_threshold=accum_threshold,
        min_river_length_m=min_river_length_m,
    )
    if stream_cb:
        try:
            stream_cb({"event_type": "terrain_milestone", "data": {"stage": "hydrology"}})
        except Exception:
            pass
    return TerrainBundle(
        height=height,
        slope=slope,
        hydrology=hydrology,
        debug={"slope": slope},
    )
