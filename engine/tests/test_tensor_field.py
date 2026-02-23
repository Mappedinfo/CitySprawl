from math import acos, degrees

import numpy as np

from engine.core.geometry import Vec2
from engine.models import Point2D, RiverAreaPolygon
from engine.roads.tensor_field import TensorStreamlineConfig, build_tensor_field_grid


def _angle_deg(a: Vec2, b: Vec2) -> float:
    na = a.normalized()
    nb = b.normalized()
    if na.length() <= 1e-9 or nb.length() <= 1e-9:
        return 180.0
    return degrees(acos(max(-1.0, min(1.0, na.dot(nb)))))


def test_tensor_field_near_river_prefers_bank_tangent():
    river = RiverAreaPolygon(
        id="r0",
        points=[
            Point2D(x=20.0, y=45.0),
            Point2D(x=220.0, y=45.0),
            Point2D(x=220.0, y=75.0),
            Point2D(x=20.0, y=75.0),
        ],
        flow=20.0,
        width_mean_m=30.0,
        is_main_stem=True,
    )
    field = build_tensor_field_grid(
        extent_m=256.0,
        height=np.zeros((32, 32), dtype=np.float64),
        river_areas=[river],
        nodes=[],
        edges=[],
        hubs=[],
        cfg=TensorStreamlineConfig(tensor_grid_resolution=64),
    )
    d = field.sample_major_dir(Vec2(128.0, 90.0))
    # Near a horizontal river band, tangent should be near horizontal (or opposite horizontal).
    assert min(_angle_deg(d, Vec2(1, 0)), _angle_deg(d, Vec2(-1, 0))) < 30.0


def test_tensor_field_on_slope_prefers_contour_tangent():
    # Height increases along +x => contour tangents are vertical.
    x = np.linspace(0.0, 1.0, 64)
    h = np.tile(x[None, :], (64, 1))
    field = build_tensor_field_grid(
        extent_m=1000.0,
        height=h,
        river_areas=[],
        nodes=[],
        edges=[],
        hubs=[],
        cfg=TensorStreamlineConfig(
            tensor_grid_resolution=64,
            tensor_water_tangent_weight=0.0,
            tensor_contour_tangent_weight=2.0,
            tensor_arterial_align_weight=0.0,
            tensor_hub_attract_weight=0.0,
        ),
    )
    d = field.sample_major_dir(Vec2(500.0, 500.0))
    assert min(_angle_deg(d, Vec2(0, 1)), _angle_deg(d, Vec2(0, -1))) < 35.0
    assert field.sample_strength(Vec2(500.0, 500.0)) >= 0.0

