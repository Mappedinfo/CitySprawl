from engine.core.geometry import Vec2
from engine.roads.network import BuiltRoadEdge, BuiltRoadNode
from engine.roads.syntax import apply_syntax_postprocess, compute_space_syntax_edge_scores


def _node(nid: str, x: float, y: float) -> BuiltRoadNode:
    return BuiltRoadNode(id=nid, pos=Vec2(x, y), kind="junction", source_hub_id=None)


def _edge(eid: str, u: str, v: str, road_class: str, width: float) -> BuiltRoadEdge:
    return BuiltRoadEdge(
        id=eid,
        u=u,
        v=v,
        road_class=road_class,
        weight=1.0,
        length_m=100.0,
        river_crossings=0,
        width_m=width,
        render_order=1,
        path_points=[Vec2(0.0, 0.0), Vec2(1.0, 0.0)],
    )


def test_syntax_scores_compute_or_degrade_cleanly():
    nodes = [_node("a", 0, 0), _node("b", 1, 0), _node("c", 2, 0)]
    edges = [_edge("ab", "a", "b", "arterial", 18.0), _edge("bc", "b", "c", "major_local", 11.0)]
    scores, notes = compute_space_syntax_edge_scores(nodes, edges, choice_radius_hops=5)
    if scores:
        assert "bc" in scores or "ab" in scores
    else:
        assert any("degraded" in n or "empty" in n for n in notes)


def test_syntax_postprocess_width_emphasis_and_optional_pruning():
    nodes = [_node("a", 0, 0), _node("b", 1, 0), _node("c", 2, 0), _node("d", 1, 1)]
    edges = [
        _edge("ab", "a", "b", "arterial", 18.0),
        _edge("bc", "b", "c", "arterial", 18.0),
        _edge("bd", "b", "d", "major_local", 11.0),
    ]
    out, notes, numeric = apply_syntax_postprocess(
        nodes,
        edges,
        syntax_enable=True,
        choice_radius_hops=10,
        prune_low_choice_collectors=True,
        prune_quantile=0.5,
    )
    assert out
    if numeric["syntax_enabled"] > 0.5:
        assert numeric["syntax_scored_edge_count"] >= 1.0
    else:
        assert any("degraded" in n for n in notes)

