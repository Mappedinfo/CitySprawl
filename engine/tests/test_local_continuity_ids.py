from collections import defaultdict

from engine.generator import generate_city
from engine.models import GenerateConfig


def test_local_edges_export_continuity_ids_and_segment_order():
    artifact = generate_city(
        GenerateConfig(
            seed=37,
            extent_m=5000.0,
            grid_resolution=64,
            hubs={"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 220.0},
            roads={
                "k_neighbors": 4,
                "loop_budget": 2,
                "branch_steps": 1,
                "collector_spacing_m": 360.0,
                "local_spacing_m": 130.0,
                "collector_generator": "classic_turtle",
                "local_generator": "classic_sprawl",
                "local_geometry_mode": "classic_sprawl_rerouted",
                "local_reroute_coverage": "selective",
                "local_classic_probe_step_m": 16.0,
                "local_classic_seed_spacing_m": 110.0,
                "syntax_enable": True,
            },
        )
    )

    local_edges = [e for e in artifact.roads.edges if e.road_class == "minor_local"]
    assert local_edges
    assert all(e.continuity_id for e in local_edges)
    assert all(e.segment_order is not None for e in local_edges)

    by_continuity = defaultdict(list)
    all_cont_ids = {str(e.continuity_id) for e in local_edges if e.continuity_id}
    for edge in local_edges:
        by_continuity[str(edge.continuity_id)].append(edge)

    # At least one continuity should span multiple topology-split edges.
    assert any(len(group) > 1 for group in by_continuity.values())

    for cid, group in by_continuity.items():
        orders = sorted(int(e.segment_order) for e in group if e.segment_order is not None)
        assert orders == list(range(len(orders))), f"{cid} has non-contiguous segment_order: {orders}"
        for e in group:
            if e.parent_continuity_id is not None:
                assert str(e.parent_continuity_id) in all_cont_ids
