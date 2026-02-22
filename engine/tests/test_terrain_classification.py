import numpy as np

from engine.terrain.classification import compute_terrain_classification
from engine.terrain.generator import compute_slope, generate_heightmap


def test_terrain_classification_is_deterministic_and_bounded():
    extent = 512.0
    height = generate_heightmap(resolution=96, octaves=4, seed=11, relief_strength=1.0)
    slope = compute_slope(height, extent)
    river_polylines = [
        {
            "id": "r0",
            "points": [{"x": 10.0, "y": 10.0}, {"x": 128.0, "y": 120.0}, {"x": 260.0, "y": 260.0}],
            "flow": 20.0,
            "length_m": 300.0,
        }
    ]

    a = compute_terrain_classification(height, slope, extent_m=extent, river_polylines=river_polylines, max_resolution=64)
    b = compute_terrain_classification(height, slope, extent_m=extent, river_polylines=river_polylines, max_resolution=64)

    assert a.terrain_class_preview.shape == b.terrain_class_preview.shape
    assert np.array_equal(a.terrain_class_preview, b.terrain_class_preview)
    assert np.all(a.terrain_class_preview >= 0)
    assert np.all(a.terrain_class_preview <= 5)
    assert np.min(a.hillshade_preview) >= 0.0
    assert np.max(a.hillshade_preview) <= 1.0

