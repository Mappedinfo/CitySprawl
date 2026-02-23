from __future__ import annotations

from shapely.geometry import Point, Polygon

from engine.core.geometry import Vec2
from engine.roads.local_reroute import build_local_routing_corridor


def test_build_local_routing_corridor_respects_block_and_river_setback():
    trace = [Vec2(10.0, 10.0), Vec2(60.0, 35.0), Vec2(120.0, 20.0)]
    block = Polygon([(0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)])
    river = Polygon([(45.0, -10.0), (55.0, -10.0), (55.0, 90.0), (45.0, 90.0)])
    corridor = build_local_routing_corridor(
        trace,
        block_poly=block,
        river_union=river,
        corridor_buffer_m=20.0,
        block_margin_m=2.0,
        river_setback_m=8.0,
    )
    assert corridor is not None
    assert not corridor.is_empty
    assert not corridor.covers(Point(110.0, 20.0))  # clipped by block
    assert not corridor.covers(Point(50.0, 20.0))   # removed by river setback


def test_build_local_routing_corridor_corridor_only_fallback_works():
    trace = [Vec2(0.0, 0.0), Vec2(50.0, 10.0)]
    corridor = build_local_routing_corridor(
        trace,
        block_poly=None,
        river_union=None,
        corridor_buffer_m=10.0,
        block_margin_m=0.0,
        river_setback_m=0.0,
    )
    assert corridor is not None
    assert not corridor.is_empty
    assert corridor.covers(Point(25.0, 5.0))
