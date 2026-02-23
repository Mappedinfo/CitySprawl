import numpy as np
from shapely.geometry import Point, Polygon

from engine.core.geometry import Vec2
from engine.hubs.sampling import HubPoint
from engine.roads.classic_growth import ClassicCollectorConfig, generate_classic_collectors
from engine.roads.network import BuiltRoadEdge, BuiltRoadNode


def test_classic_turtle_collectors_generate_and_respect_river_setback():
    nodes = [
        BuiltRoadNode(id="h0", pos=Vec2(120.0, 200.0), kind="hub", source_hub_id="h0"),
        BuiltRoadNode(id="h1", pos=Vec2(380.0, 200.0), kind="hub", source_hub_id="h1"),
    ]
    edges = [
        BuiltRoadEdge(
            id="e0",
            u="h0",
            v="h1",
            road_class="arterial",
            weight=1.0,
            length_m=260.0,
            river_crossings=0,
            width_m=18.0,
            render_order=0,
            path_points=[Vec2(120.0, 200.0), Vec2(380.0, 200.0)],
        )
    ]
    hubs = [
        HubPoint(id="h0", pos=Vec2(120.0, 200.0), tier=1, score=1.0, attrs={}),
        HubPoint(id="h1", pos=Vec2(380.0, 200.0), tier=2, score=0.8, attrs={}),
    ]
    river_union = Polygon([(220.0, 120.0), (280.0, 120.0), (280.0, 280.0), (220.0, 280.0)])
    blocks = [
        Polygon([(0.0, 0.0), (220.0, 0.0), (220.0, 160.0), (0.0, 160.0)]),
        Polygon([(280.0, 240.0), (500.0, 240.0), (500.0, 400.0), (280.0, 400.0)]),
        Polygon([(0.0, 160.0), (220.0, 160.0), (220.0, 400.0), (0.0, 400.0)]),
        Polygon([(180.0, 300.0), (340.0, 300.0), (340.0, 420.0), (180.0, 420.0)]),
    ]
    x = np.linspace(0.0, 1.0, 64)
    height = np.tile(x[None, :], (64, 1))
    slope = np.ones((64, 64), dtype=np.float64) * 0.35
    river_mask = np.zeros((64, 64), dtype=bool)
    traces, cul_flags, notes, numeric = generate_classic_collectors(
        extent_m=500.0,
        height=height,
        slope=slope,
        river_mask=river_mask,
        river_areas=[],
        river_union=river_union,
        nodes=nodes,
        edges=edges,
        hubs=hubs,
        blocks=blocks,
        cfg=ClassicCollectorConfig(
            classic_seed_spacing_m=120.0,
            classic_probe_step_m=18.0,
            classic_max_trace_len_m=600.0,
            classic_min_trace_len_m=60.0,
            classic_branch_prob=0.45,
            classic_continue_prob=0.95,
            classic_culdesac_prob=0.25,
            classic_max_segments=40,
            classic_max_queue_size=200,
            river_setback_m=15.0,
            river_snap_dist_m=30.0,
        ),
        seed=17,
    )
    assert any(n.startswith("classic_trace_count:") for n in notes)
    assert len(traces) > 0
    assert len(cul_flags) == len(traces)
    assert numeric.get("collector_classic_enabled", 0.0) > 0.5
    assert numeric.get("collector_classic_riverfront_seed_count", 0.0) > 0.0
    assert "collector_classic_arterial_t_attach_count" in numeric
    assert "collector_classic_network_attach_fallback_count" in numeric
    forbidden = river_union.buffer(15.0)
    for tr in traces:
        assert len(tr) >= 2
        for p in tr:
            assert not forbidden.contains(Point(p.x, p.y))
