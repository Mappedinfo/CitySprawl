from __future__ import annotations

import numpy as np

from engine.core.geometry import Vec2
from engine.roads.network import BuiltRoadEdge, BuiltRoadNode, _dedupe_and_snap, _route_all_edges
from engine.roads.syntax import apply_syntax_postprocess


def _node(nid: str, x: float, y: float, kind: str = "junction") -> BuiltRoadNode:
    return BuiltRoadNode(id=nid, pos=Vec2(x, y), kind=kind, source_hub_id=None)


def _edge(
    eid: str,
    u: str,
    v: str,
    road_class: str,
    pts: list[Vec2],
    *,
    flags: frozenset[str] | None = None,
) -> BuiltRoadEdge:
    return BuiltRoadEdge(
        id=eid,
        u=u,
        v=v,
        road_class=road_class,
        weight=1.0,
        length_m=1.0,
        river_crossings=0,
        width_m=11.0 if road_class == "collector" else 6.0,
        render_order=1 if road_class == "collector" else 2,
        path_points=pts,
        flags=flags or frozenset(),
    )


def test_culdesac_flags_survive_dedupe_route_and_syntax():
    nodes = [
        _node("a0", 0.0, 0.0, "hub"),
        _node("a1", 10.0, 0.0, "hub"),
        _node("a2", 20.0, 0.0, "hub"),
        _node("a3", 30.0, 0.0, "hub"),
        _node("l0", 10.0, 5.0),
        _node("l1", 14.0, 8.0),
        _node("l0b", 10.0, 5.0),
        _node("l1b", 14.0, 8.0),
    ]
    edges = [
        _edge("c0", "a0", "a1", "collector", [Vec2(0.0, 0.0), Vec2(10.0, 0.0)]),
        _edge("c1-cul", "a1", "a2", "collector", [Vec2(10.0, 0.0), Vec2(20.0, 0.0)], flags=frozenset({"culdesac"})),
        _edge("c2", "a2", "a3", "collector", [Vec2(20.0, 0.0), Vec2(30.0, 0.0)]),
        _edge(
            "l-cul",
            "l0",
            "l1",
            "local",
            [Vec2(10.0, 5.0), Vec2(14.0, 8.0), Vec2(17.0, 9.0)],
            flags=frozenset({"culdesac", "local_rerouted"}),
        ),
        _edge("l-dup", "l0b", "l1b", "local", [Vec2(10.0, 5.0), Vec2(14.0, 8.0)]),
    ]

    nodes2, edges2, _ = _dedupe_and_snap(nodes, edges, snap_tol=0.5)
    local_edges = [e for e in edges2 if e.road_class == "local"]
    assert len(local_edges) == 1
    assert "culdesac" in set(local_edges[0].flags)
    assert "local_rerouted" in set(local_edges[0].flags)
    assert "-cul" in str(local_edges[0].id)

    slope = np.zeros((32, 32), dtype=np.float64)
    river_mask = np.zeros((32, 32), dtype=bool)
    _route_all_edges(
        nodes=nodes2,
        edges=edges2,
        extent_m=40.0,
        slope=slope,
        river_mask=river_mask,
        slope_penalty=1.0,
        river_cross_penalty=10.0,
    )
    local_after_route = [e for e in edges2 if e.road_class == "local"][0]
    assert "culdesac" in set(local_after_route.flags)
    assert "local_rerouted" in set(local_after_route.flags)
    assert "-cul" in str(local_after_route.id)

    edges3, _, _ = apply_syntax_postprocess(
        nodes=nodes2,
        edges=list(edges2),
        syntax_enable=True,
        choice_radius_hops=10,
        prune_low_choice_collectors=False,
        prune_quantile=0.15,
    )
    flagged_edges = [e for e in edges3 if "-cul" in str(getattr(e, "id", ""))]
    assert flagged_edges
    assert all("culdesac" in set(getattr(e, "flags", [])) for e in flagged_edges)
    assert any("local_rerouted" in set(getattr(e, "flags", [])) for e in flagged_edges if str(getattr(e, "road_class", "")) == "local")
