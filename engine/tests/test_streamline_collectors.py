import numpy as np
from shapely.geometry import Point, Polygon

from engine.core.geometry import Vec2
from engine.hubs.sampling import HubPoint
from engine.roads.network import BuiltRoadEdge, BuiltRoadNode
from engine.roads.tensor_field import TensorStreamlineConfig, generate_tensor_collectors


def test_tensor_streamline_collectors_generate_and_avoid_river_setback():
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
    hubs = [HubPoint(id="h0", pos=Vec2(120.0, 200.0), tier=1, score=1.0, attrs={}), HubPoint(id="h1", pos=Vec2(380.0, 200.0), tier=2, score=0.8, attrs={})]
    river_union = Polygon([(220.0, 120.0), (280.0, 120.0), (280.0, 280.0), (220.0, 280.0)])
    blocks = [
        Polygon([(0.0, 0.0), (220.0, 0.0), (220.0, 160.0), (0.0, 160.0)]),
        Polygon([(280.0, 240.0), (500.0, 240.0), (500.0, 400.0), (280.0, 400.0)]),
    ]
    x = np.linspace(0.0, 1.0, 64)
    height = np.tile(x[None, :], (64, 1))
    river_mask = np.zeros((64, 64), dtype=bool)
    traces, notes = generate_tensor_collectors(
        extent_m=500.0,
        height=height,
        river_mask=river_mask,
        river_areas=[],
        river_union=river_union,
        nodes=nodes,
        edges=edges,
        hubs=hubs,
        blocks=blocks,
        cfg=TensorStreamlineConfig(
            tensor_grid_resolution=64,
            tensor_seed_spacing_m=140.0,
            tensor_step_m=20.0,
            tensor_max_trace_len_m=600.0,
            tensor_min_trace_len_m=80.0,
            river_setback_m=15.0,
            tensor_contour_tangent_weight=2.2,
            tensor_arterial_align_weight=0.4,
            tensor_hub_attract_weight=0.1,
        ),
        seed=17,
    )
    assert any(n.startswith("tensor_trace_count:") for n in notes)
    assert len(traces) > 0
    forbidden = river_union.buffer(15.0)
    for tr in traces:
        assert len(tr) >= 2
        for p in tr:
            assert not forbidden.contains(Point(p.x, p.y))
