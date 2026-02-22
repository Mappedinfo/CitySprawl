from __future__ import annotations

from typing import Sequence

import numpy as np

from engine.models import ResourceSite


_RESOURCE_WEIGHTS = {
    'water': 0.55,
    'agri': 0.45,
    'ore': 0.25,
    'forest': 0.18,
}


def compute_population_potential(
    suitability: np.ndarray,
    flood_risk: np.ndarray,
    resource_sites: Sequence[ResourceSite],
    extent_m: float,
) -> np.ndarray:
    rows, cols = suitability.shape
    if rows == 0 or cols == 0:
        return np.zeros_like(suitability, dtype=np.float64)

    xs = np.linspace(0.0, extent_m, cols)
    ys = np.linspace(0.0, extent_m, rows)
    xx, yy = np.meshgrid(xs, ys)

    influence = np.zeros_like(suitability, dtype=np.float64)
    for site in resource_sites:
        weight = _RESOURCE_WEIGHTS.get(site.kind, 0.15) * float(site.quality)
        radius = max(1.0, float(site.influence_radius_m))
        dx = xx - float(site.x)
        dy = yy - float(site.y)
        influence += weight * np.exp(-(dx * dx + dy * dy) / (2.0 * radius * radius))

    if influence.size:
        influence = influence / (float(np.max(influence)) + 1e-9)

    potential = 0.62 * np.clip(suitability, 0.0, 1.0) + 0.38 * np.clip(influence, 0.0, 1.0)
    potential *= (1.0 - 0.55 * np.clip(flood_risk, 0.0, 1.0))
    return np.clip(potential, 0.0, 1.0)
