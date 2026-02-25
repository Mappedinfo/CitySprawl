import numpy as np
from shapely.geometry import Polygon, Point

from engine.core.geometry import Segment, Vec2
from engine.hubs.sampling import HubPoint
from engine.roads.classic_local_fill import (
    LocalClassicFillConfig,
    _classify_network_contact_mode,
    generate_classic_local_fill,
)
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
    assert "local_classic_stop_near_network_count" in numeric
    assert "local_classic_stop_block_exit_count" in numeric
    assert "local_classic_stop_stochastic_stop_count" in numeric
    assert "local_classic_stop_road_too_far_count" in numeric
    assert "local_classic_contact_opposing_count" in numeric
    assert "local_classic_contact_parallel_count" in numeric
    assert "local_classic_contact_perpendicular_continue_count" in numeric
    assert "local_classic_contact_oblique_continue_count" in numeric
    assert len(traces) > 0
    assert len(cul_flags) == len(traces)
    assert len(trace_meta) == len(traces)
    assert any(hasattr(m, "block_idx") for m in trace_meta)
    forbidden = river_union.buffer(12.0)
    for tr in traces:
        for p in tr:
            assert not forbidden.contains(Point(p.x, p.y))


def test_classic_local_fill_emits_trace_length_stats_and_hits_target_band_on_large_blocks():
    nodes = [
        BuiltRoadNode(id="a0", pos=Vec2(120.0, 220.0), kind="hub"),
        BuiltRoadNode(id="a1", pos=Vec2(2080.0, 220.0), kind="hub"),
        BuiltRoadNode(id="c0", pos=Vec2(220.0, 120.0), kind="hub"),
        BuiltRoadNode(id="c1", pos=Vec2(220.0, 1880.0), kind="hub"),
    ]
    edges = [
        BuiltRoadEdge(
            id="art0",
            u="a0",
            v="a1",
            road_class="arterial",
            weight=1.0,
            length_m=1960.0,
            river_crossings=0,
            width_m=18.0,
            render_order=0,
            path_points=[Vec2(120.0, 220.0), Vec2(2080.0, 220.0)],
        ),
        BuiltRoadEdge(
            id="col0",
            u="c0",
            v="c1",
            road_class="collector",
            weight=1.0,
            length_m=1760.0,
            river_crossings=0,
            width_m=11.0,
            render_order=1,
            path_points=[Vec2(220.0, 120.0), Vec2(220.0, 1880.0)],
        ),
    ]
    blocks = [Polygon([(80.0, 80.0), (2120.0, 80.0), (2120.0, 1920.0), (80.0, 1920.0)])]
    hubs = [HubPoint(id="h0", pos=Vec2(200.0, 200.0), tier=1, score=1.0, attrs={})]
    height = np.zeros((128, 128), dtype=np.float64)
    slope = np.ones((128, 128), dtype=np.float64) * 0.05
    river_mask = np.zeros((128, 128), dtype=bool)
    river_union = Polygon()

    traces, cul_flags, _trace_meta, notes, numeric = generate_classic_local_fill(
        extent_m=2200.0,
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
            local_spacing_m=130.0,
            local_classic_probe_step_m=20.0,
            local_classic_seed_spacing_m=120.0,
            local_classic_min_trace_len_m=80.0,
            local_classic_continue_prob=0.96,
            local_classic_branch_prob=0.08,
            local_classic_culdesac_prob=0.15,
            local_classic_max_segments_per_block=10,
            local_classic_max_road_distance_m=2500.0,
            local_community_seed_count_per_block=2,
        ),
        seed=19,
    )

    lengths = []
    for tr in traces:
        total = 0.0
        for i in range(len(tr) - 1):
            total += tr[i].distance_to(tr[i + 1])
        lengths.append(total)

    assert len(traces) > 0
    assert len(cul_flags) == len(traces)
    assert any(n.startswith("local_classic_trace_len_m:") for n in notes)
    assert "local_classic_trace_len_p50_m" in numeric
    assert "local_classic_trace_target_band_rate" in numeric
    # Coverage-first default broadens applicability to smaller blocks and
    # reduces reliance on ultra-long traces; keep a floor aligned to the new
    # acceptance criteria instead of the prior long-trace-biased threshold.
    assert numeric["local_classic_trace_len_p50_m"] >= 220.0
    assert numeric.get("local_classic_trace_len_p90_m", 0.0) >= 500.0
    assert any(500.0 <= l <= 1000.0 for l in lengths)
    assert numeric["local_classic_trace_target_band_rate"] > 0.0
    assert numeric.get("local_classic_stop_road_too_far_count", 0.0) == 0.0


def test_classic_local_fill_ignores_hard_max_distance_stop_in_coverage_first_mode():
    nodes = [
        BuiltRoadNode(id="a0", pos=Vec2(100.0, 200.0), kind="hub"),
        BuiltRoadNode(id="a1", pos=Vec2(1900.0, 200.0), kind="hub"),
        BuiltRoadNode(id="c0", pos=Vec2(220.0, 120.0), kind="hub"),
        BuiltRoadNode(id="c1", pos=Vec2(220.0, 2080.0), kind="hub"),
    ]
    edges = [
        BuiltRoadEdge(
            id="art0",
            u="a0",
            v="a1",
            road_class="arterial",
            weight=1.0,
            length_m=1800.0,
            river_crossings=0,
            width_m=18.0,
            render_order=0,
            path_points=[Vec2(100.0, 200.0), Vec2(1900.0, 200.0)],
        ),
        BuiltRoadEdge(
            id="col0",
            u="c0",
            v="c1",
            road_class="collector",
            weight=1.0,
            length_m=1960.0,
            river_crossings=0,
            width_m=11.0,
            render_order=1,
            path_points=[Vec2(220.0, 120.0), Vec2(220.0, 2080.0)],
        ),
    ]
    blocks = [Polygon([(60.0, 60.0), (2140.0, 60.0), (2140.0, 2140.0), (60.0, 2140.0)])]
    hubs = [HubPoint(id="h0", pos=Vec2(220.0, 220.0), tier=1, score=1.0, attrs={})]
    height = np.zeros((128, 128), dtype=np.float64)
    slope = np.ones((128, 128), dtype=np.float64) * 0.05
    river_mask = np.zeros((128, 128), dtype=bool)
    river_union = Polygon()

    traces, _cul_flags, _trace_meta, _notes, numeric = generate_classic_local_fill(
        extent_m=2200.0,
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
            local_spacing_m=130.0,
            local_classic_probe_step_m=18.0,
            local_classic_seed_spacing_m=110.0,
            local_classic_min_trace_len_m=60.0,
            local_classic_continue_prob=0.95,
            local_classic_branch_prob=0.10,
            local_classic_culdesac_prob=0.15,
            local_classic_max_segments_per_block=8,
            local_classic_max_road_distance_m=140.0,  # formerly caused hard-stop churn
            local_community_seed_count_per_block=2,
        ),
        seed=23,
    )

    assert len(traces) > 0
    assert numeric.get("local_classic_stop_road_too_far_count", 0.0) == 0.0
    assert numeric.get("local_classic_trace_len_p50_m", 0.0) >= 180.0


def test_classic_local_fill_mainlines_continue_through_perpendicular_network_contacts():
    nodes = [
        BuiltRoadNode(id="c0", pos=Vec2(300.0, 80.0), kind="hub"),
        BuiltRoadNode(id="c1", pos=Vec2(300.0, 520.0), kind="hub"),
    ]
    edges = [
        BuiltRoadEdge(
            id="col0",
            u="c0",
            v="c1",
            road_class="collector",
            weight=1.0,
            length_m=440.0,
            river_crossings=0,
            width_m=11.0,
            render_order=1,
            path_points=[Vec2(300.0, 80.0), Vec2(300.0, 520.0)],
        ),
    ]
    blocks = [Polygon([(60.0, 60.0), (540.0, 60.0), (540.0, 540.0), (60.0, 540.0)])]
    hubs = [HubPoint(id="h0", pos=Vec2(300.0, 300.0), tier=1, score=1.0, attrs={})]
    height = np.zeros((96, 96), dtype=np.float64)
    slope = np.zeros((96, 96), dtype=np.float64)
    river_mask = np.zeros((96, 96), dtype=bool)
    river_union = Polygon()

    traces, _cul_flags, _trace_meta, _notes, numeric = generate_classic_local_fill(
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
            local_spacing_m=120.0,
            local_classic_probe_step_m=16.0,
            local_classic_seed_spacing_m=220.0,
            local_classic_min_trace_len_m=40.0,
            local_classic_max_trace_len_m=120.0,  # root mainlines should exceed this soft cap
            local_classic_continue_prob=0.02,  # root/depth<=1 should not stochastic-stop
            local_classic_branch_prob=0.0,
            local_classic_culdesac_prob=0.5,
            local_classic_max_segments_per_block=8,
            local_collector_follow_weight=0.0,
            local_community_spine_prob=0.0,
            local_classic_turn_limit_deg=18.0,
            local_community_seed_count_per_block=1,
        ),
        seed=7,
    )

    def _trace_len(tr):
        return sum(tr[i].distance_to(tr[i + 1]) for i in range(len(tr) - 1))

    def _is_horizontal_crossing_trace(tr):
        xs = [p.x for p in tr]
        ys = [p.y for p in tr]
        return min(xs) < 260.0 and max(xs) > 340.0 and (max(ys) - min(ys)) < 90.0

    lengths = [_trace_len(tr) for tr in traces]
    crossing_lengths = [l for tr, l in zip(traces, lengths) if _is_horizontal_crossing_trace(tr)]

    assert len(traces) > 0
    assert numeric.get("local_classic_stop_stochastic_stop_count", 0.0) == 0.0
    assert any(l > 220.0 for l in lengths)
    assert any(l > 120.0 for l in lengths)
    # A horizontal local trace should pass through the central vertical collector
    # (perpendicular contact) instead of terminating immediately at the junction.
    assert any(l > 260.0 for l in crossing_lengths)
    assert numeric.get("local_classic_contact_perpendicular_continue_count", 0.0) >= 1.0


def test_classify_network_contact_mode_emits_opposing_parallel_and_perpendicular():
    segs = [Segment(Vec2(0.0, 0.0), Vec2(10.0, 0.0))]
    cp = Vec2(5.0, 0.0)
    assert _classify_network_contact_mode(approach_dir=Vec2(-1.0, 0.0), contact_point=cp, candidate_segments=segs) == "opposing"
    assert _classify_network_contact_mode(approach_dir=Vec2(1.0, 0.0), contact_point=cp, candidate_segments=segs) == "parallel"
    assert _classify_network_contact_mode(approach_dir=Vec2(0.0, 1.0), contact_point=cp, candidate_segments=segs) == "perpendicular"
    assert _classify_network_contact_mode(
        approach_dir=Vec2(1.0, 1.0).normalized(),
        contact_point=cp,
        candidate_segments=segs,
    ) == "oblique"
