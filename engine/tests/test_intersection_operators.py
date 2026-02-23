from engine.core.geometry import Vec2
from engine.roads.intersections import apply_intersection_operators, split_crossings
from engine.roads.network import BuiltRoadEdge, BuiltRoadNode


def _node(nid: str, x: float, y: float, kind: str = "junction") -> BuiltRoadNode:
    return BuiltRoadNode(id=nid, pos=Vec2(x, y), kind=kind, source_hub_id=None)


def _edge(eid: str, u: str, v: str, road_class: str, pts: list[Vec2]) -> BuiltRoadEdge:
    return BuiltRoadEdge(
        id=eid,
        u=u,
        v=v,
        road_class=road_class,
        weight=1.0,
        length_m=1.0,
        river_crossings=0,
        width_m=11.0 if road_class == "collector" else 18.0,
        render_order=1 if road_class == "collector" else 0,
        path_points=pts,
    )


def test_t_junction_operator_snaps_collector_and_splits_target():
    nodes = [
        _node("a0", 0.0, 0.0, "hub"),
        _node("a1", 10.0, 0.0, "hub"),
        _node("c0", 5.0, 4.0),
        _node("c1", 5.0, 1.2),
    ]
    edges = [
        _edge("art", "a0", "a1", "arterial", [Vec2(0.0, 0.0), Vec2(10.0, 0.0)]),
        _edge("col", "c0", "c1", "collector", [Vec2(5.0, 4.0), Vec2(5.0, 1.2)]),
    ]
    nodes2, edges2, notes, numeric = apply_intersection_operators(
        nodes,
        edges,
        snap_radius_m=0.5,
        t_junction_radius_m=2.0,
        split_tolerance_m=0.5,
        min_dangle_length_m=0.0,
    )
    assert nodes2
    assert len(edges2) >= 3  # arterial should be split
    assert numeric["intersection_t_junction_count"] >= 1.0
    assert any("t_junction" in n for n in notes)
    collector = next(e for e in edges2 if str(e.road_class) == "collector")
    assert collector.path_points is not None
    assert abs(float(collector.path_points[-1].y)) < 1e-6


def test_crossing_split_splits_x_intersection():
    nodes = [
        _node("n0", 0.0, 0.0, "hub"),
        _node("n1", 10.0, 10.0, "hub"),
        _node("n2", 0.0, 10.0, "hub"),
        _node("n3", 10.0, 0.0, "hub"),
    ]
    edges = [
        _edge("e0", "n0", "n1", "arterial", [Vec2(0.0, 0.0), Vec2(10.0, 10.0)]),
        _edge("e1", "n2", "n3", "arterial", [Vec2(0.0, 10.0), Vec2(10.0, 0.0)]),
    ]
    edges2, split_count = split_crossings(nodes, edges, split_tol_m=0.5)
    assert split_count >= 1
    assert len(edges2) >= 4


def test_split_preserves_culdesac_suffix_on_split_children():
    nodes = [
        _node("n0", 0.0, 0.0, "hub"),
        _node("n1", 10.0, 10.0, "hub"),
        _node("n2", 0.0, 10.0, "hub"),
        _node("n3", 10.0, 0.0, "hub"),
    ]
    edges = [
        _edge("e0-cul", "n0", "n1", "arterial", [Vec2(0.0, 0.0), Vec2(10.0, 10.0)]),
        _edge("e1", "n2", "n3", "arterial", [Vec2(0.0, 10.0), Vec2(10.0, 0.0)]),
    ]
    edges2, split_count = split_crossings(nodes, edges, split_tol_m=0.5)
    assert split_count >= 1
    assert any("-cul" in str(e.id) for e in edges2 if str(e.road_class) == "arterial")


def test_prune_short_dangles_keeps_marked_culdesac():
    nodes = [
        _node("a0", 0.0, 0.0, "hub"),
        _node("a1", 10.0, 0.0, "hub"),
        _node("c0", 5.0, 0.0),
        _node("c1", 5.0, 2.0),
    ]
    edges = [
        _edge("art", "a0", "a1", "arterial", [Vec2(0.0, 0.0), Vec2(10.0, 0.0)]),
        _edge("col-cul", "c0", "c1", "collector", [Vec2(5.0, 0.0), Vec2(5.0, 2.0)]),
    ]
    _, edges2, _, numeric = apply_intersection_operators(
        nodes,
        edges,
        snap_radius_m=0.0,
        t_junction_radius_m=0.0,
        split_tolerance_m=0.5,
        min_dangle_length_m=10.0,
    )
    assert any(str(e.id) == "col-cul" for e in edges2)
    assert numeric["intersection_pruned_dangle_count"] == 0.0
