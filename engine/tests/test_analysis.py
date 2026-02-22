import numpy as np

from engine.analysis import compute_population_potential, compute_suitability_and_flood, generate_resource_sites
from engine.core.geometry import Vec2


def _mock_river():
    return [{"id": "r0", "points": [Vec2(10.0, 10.0), Vec2(500.0, 500.0)], "flow": 120.0, "length_m": 700.0}]


def test_suitability_and_flood_deterministic_shapes_and_ranges():
    rng = np.random.default_rng(0)
    height = rng.random((64, 64))
    slope = np.abs(np.gradient(height)[0])
    out1 = compute_suitability_and_flood(height, slope, 1024.0, _mock_river(), max_resolution=64)
    out2 = compute_suitability_and_flood(height, slope, 1024.0, _mock_river(), max_resolution=64)
    assert out1.suitability.shape == out1.flood_risk.shape == (64, 64)
    assert np.all((out1.suitability >= 0.0) & (out1.suitability <= 1.0))
    assert np.all((out1.flood_risk >= 0.0) & (out1.flood_risk <= 1.0))
    assert np.allclose(out1.suitability, out2.suitability)
    assert np.allclose(out1.flood_risk, out2.flood_risk)


def test_resources_and_population_nonempty_and_stable_counts():
    rng = np.random.default_rng(1)
    height = rng.random((96, 96))
    slope = np.abs(np.gradient(height)[0])
    surfaces = compute_suitability_and_flood(height, slope, 1200.0, _mock_river(), max_resolution=96)
    sites1 = generate_resource_sites(
        seed=9,
        extent_m=1200.0,
        suitability=surfaces.suitability,
        flood_risk=surfaces.flood_risk,
        height_preview=surfaces.height_preview,
        slope_preview=surfaces.slope_preview,
        river_polylines=_mock_river(),
    )
    sites2 = generate_resource_sites(
        seed=9,
        extent_m=1200.0,
        suitability=surfaces.suitability,
        flood_risk=surfaces.flood_risk,
        height_preview=surfaces.height_preview,
        slope_preview=surfaces.slope_preview,
        river_polylines=_mock_river(),
    )
    assert len(sites1) > 0
    assert [s.kind for s in sites1] == [s.kind for s in sites2]
    pop = compute_population_potential(surfaces.suitability, surfaces.flood_risk, sites1, 1200.0)
    assert pop.shape == surfaces.suitability.shape
    assert float(np.max(pop)) <= 1.0 + 1e-9
