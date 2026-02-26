import numpy as np

from engine.core.geometry import Vec2
from engine.roads.terrain_probe import TerrainProbe, TerrainProbeConfig


def _make_probe() -> TerrainProbe:
    # Height rises along +x; contour direction should be roughly +/-y.
    x = np.linspace(0.0, 1.0, 64)
    height = np.tile(x[None, :], (64, 1))
    slope = np.ones((64, 64), dtype=np.float64) * 0.5  # ~26.5 deg
    river_mask = np.zeros((64, 64), dtype=bool)
    river_mask[:, 30:34] = True
    river_area = {
        "id": "r0",
        "points": [
            {"x": 240.0, "y": 0.0},
            {"x": 272.0, "y": 0.0},
            {"x": 272.0, "y": 512.0},
            {"x": 240.0, "y": 512.0},
        ],
    }
    return TerrainProbe(
        extent_m=512.0,
        height=height,
        slope=slope,
        river_mask=river_mask,
        river_areas=[river_area],
        river_union=None,
        cfg=TerrainProbeConfig(
            slope_straight_threshold_deg=5.0,
            slope_serpentine_threshold_deg=15.0,
            slope_hard_limit_deg=22.0,
            contour_follow_weight=1.0,
            river_snap_dist_m=32.0,
            river_parallel_bias_weight=1.0,
            river_avoid_weight=1.0,
            river_setback_m=0.0,
        ),
    )


def test_terrain_probe_contour_bias_on_steep_slope():
    probe = _make_probe()
    p = Vec2(120.0, 240.0)
    current = Vec2(1.0, 0.0)
    out = probe.adjust_direction_for_slope(p, current, road_class="major_local")
    contour = probe.sample_contour_dir(p)
    grad = probe.sample_gradient_dir(p)
    assert probe.sample_slope_deg(p) > 15.0
    assert abs(out.dot(contour.normalized())) > 0.5
    assert abs(out.dot(grad.normalized())) < 0.8


def test_terrain_probe_detects_water_and_biases_to_river_tangent():
    probe = _make_probe()
    water_point = Vec2(256.0, 220.0)
    assert probe.check_water_hit(water_point)
    near_bank = Vec2(226.0, 220.0)
    tan, dist = probe.nearest_river_bank_tangent(near_bank)
    assert tan is not None
    assert dist >= 0.0
    out = probe.snap_or_bias_to_riverfront(near_bank, Vec2(1.0, 0.0))
    # River polygon is vertical; tangent should be mostly +/-y.
    assert abs(out.y) >= abs(out.x)

