from __future__ import annotations

from shapely.geometry import Polygon

from engine.core.geometry import Vec2
from engine.roads.local_reroute import (
    LocalRerouteConfig,
    reroute_local_polyline,
    select_local_reroute_candidates,
)


def test_select_local_reroute_candidates_selective_prioritizes_connectors_and_spines():
    items = [
        {"road_class": "minor_local", "length_m": 40.0, "flags": set(), "meta": {"is_spine_candidate": False, "connected_to_collector": False}},
        {"road_class": "minor_local", "length_m": 120.0, "flags": set(), "meta": {"is_spine_candidate": False, "connected_to_collector": False}},
        {"road_class": "minor_local", "length_m": 55.0, "flags": set(), "meta": {"is_spine_candidate": True, "connected_to_collector": False}},
        {"road_class": "minor_local", "length_m": 50.0, "flags": set(), "meta": {"is_spine_candidate": False, "connected_to_collector": True}},
    ]
    idxs = select_local_reroute_candidates(
        items,
        coverage="selective",
        min_length_m=70.0,
        max_edges=3,
        apply_to_grid_supplement=True,
    )
    assert len(idxs) == 3
    assert 0 not in idxs
    assert 3 in idxs  # connector should be selected


def test_reroute_local_polyline_applies_and_returns_multi_point_path():
    cfg = LocalRerouteConfig(local_reroute_smooth_iters=0, local_reroute_simplify_tol_m=0.0)
    pts = [Vec2(10.0, 10.0), Vec2(60.0, 12.0), Vec2(120.0, 10.0)]
    block = Polygon([(0.0, 0.0), (140.0, 0.0), (140.0, 80.0), (0.0, 80.0)])

    def _route(a: Vec2, b: Vec2, _corridor, _slope_scale: float, _river_scale: float):
        mid = Vec2((a.x + b.x) * 0.5, (a.y + b.y) * 0.5 + 4.0)
        return [a, mid, b]

    out, numeric, notes = reroute_local_polyline(
        pts,
        route_segment_fn=_route,
        cfg=cfg,
        block_poly=block,
        river_union=None,
        river_setback_m=0.0,
    )
    assert len(out) > 2
    assert numeric["applied"] > 0.5
    assert any(n == "local_reroute:applied" for n in notes)


def test_reroute_local_polyline_falls_back_on_route_failure():
    cfg = LocalRerouteConfig(local_reroute_smooth_iters=0)
    pts = [Vec2(10.0, 10.0), Vec2(90.0, 10.0)]

    def _route_fail(_a: Vec2, _b: Vec2, _corridor, _slope_scale: float, _river_scale: float):
        return None

    out, numeric, notes = reroute_local_polyline(
        pts,
        route_segment_fn=_route_fail,
        cfg=cfg,
        block_poly=None,
        river_union=None,
        river_setback_m=0.0,
    )
    assert out == pts
    assert numeric["fallback"] > 0.5
    assert any("fallback" in n for n in notes)
