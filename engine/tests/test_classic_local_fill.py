import numpy as np
from shapely.geometry import Polygon, Point

from engine.core.geometry import Vec2
from engine.hubs.sampling import HubPoint
from engine.roads.classic_local_fill import LocalClassicFillConfig, generate_classic_local_fill
from engine.roads.network import BuiltRoadEdge, BuiltRoadNode


def test_classic_local_fill_generates_curvy_locals_and_respects_setback():
    nodes = [
        BuiltRoadNode(id="h0", pos=Vec2(120.0, 220.0), kind="hub", source_hub_id="h0"),
        BuiltRoadNode(id="h1", pos=Vec2(420.0, 220.0), kind="hub", source_hub_id="h1"),
    ]
    edges = [
        BuiltRoadEdge(
            id="art0",
            u="h0",
            v="h1",
            road_class="arterial",
            weight=1.0,
            length_m=300.0,
            river_crossings=0,
            width_m=18.0,
            render_order=0,
            path_points=[Vec2(120.0, 220.0), Vec2(420.0, 220.0)],
        ),
        BuiltRoadEdge(
            id="col0",
            u="h0",
            v="h1",
            road_class="collector",
            weight=1.0,
            length_m=220.0,
            river_crossings=0,
            width_m=11.0,
            render_order=1,
            path_points=[Vec2(180.0, 220.0), Vec2(180.0, 360.0)],
        ),
    ]
    blocks = [Polygon([(100.0, 200.0), (460.0, 200.0), (460.0, 420.0), (100.0, 420.0)])]
    hubs = [HubPoint(id="h0", pos=Vec2(120.0, 220.0), tier=1, score=1.0, attrs={})]
    x = np.linspace(0.0, 1.0, 96)
    height = np.tile(x[None, :], (96, 1))
    slope = np.ones((96, 96), dtype=np.float64) * 0.08
    river_mask = np.zeros((96, 96), dtype=bool)
    river_union = Polygon([(300.0, 260.0), (340.0, 260.0), (340.0, 420.0), (300.0, 420.0)])

    traces, cul_flags, trace_meta, notes, numeric = generate_classic_local_fill(
        extent_m=600.0,
        height=height,
        slope=slope,
        river_mask=river_mask,
        river_areas=[],
        river_union=river_union,
        nodes=nodes,
        edges=edges,
        hubs=hubs,
        blocks=blocks,
        cfg=LocalClassicFillConfig(
            local_classic_probe_step_m=16.0,
            local_classic_seed_spacing_m=100.0,
            local_classic_max_trace_len_m=260.0,
            local_classic_min_trace_len_m=40.0,
            local_classic_continue_prob=0.72,
            local_classic_branch_prob=0.75,
            local_classic_culdesac_prob=0.6,
            local_classic_max_segments_per_block=12,
            local_community_seed_count_per_block=3,
            river_setback_m=12.0,
        ),
        seed=11,
    )
    assert any(n.startswith("local_classic_trace_count:") for n in notes)
    assert numeric.get("local_classic_enabled", 0.0) > 0.5
    assert len(traces) > 0
    assert len(cul_flags) == len(traces)
    assert len(trace_meta) == len(traces)
    assert any(hasattr(m, "block_idx") for m in trace_meta)
    forbidden = river_union.buffer(12.0)
    for tr in traces:
        for p in tr:
            assert not forbidden.contains(Point(p.x, p.y))
