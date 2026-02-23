from collections import Counter

from engine.generator import generate_city
from engine.models import GenerateConfig


def test_hierarchical_roads_emit_collector_and_dense_locals():
    artifact = generate_city(
        GenerateConfig(
            seed=17,
            extent_m=8000.0,
            grid_resolution=96,
            hubs={"t1_count": 1, "t2_count": 3, "t3_count": 12, "min_distance_m": 260.0},
            roads={
                "k_neighbors": 4,
                "loop_budget": 3,
                "branch_steps": 2,
                "slope_penalty": 2.0,
                "river_cross_penalty": 260.0,
                "style": "mixed_organic",
                "collector_spacing_m": 360.0,
                "local_spacing_m": 120.0,
                "collector_jitter": 0.14,
                "local_jitter": 0.18,
                "local_generator": "classic_sprawl",
                "local_classic_probe_step_m": 16.0,
                "local_classic_seed_spacing_m": 110.0,
                "river_setback_m": 16.0,
                "minor_bridge_budget": 4,
                "max_local_block_area_m2": 150000.0,
                "collector_generator": "classic_turtle",
                "classic_seed_spacing_m": 280.0,
                "classic_probe_step_m": 22.0,
                "classic_branch_prob": 0.4,
                "classic_continue_prob": 0.92,
                "intersection_t_junction_radius_m": 26.0,
                "local_classic_continue_prob": 0.62,
                "local_classic_culdesac_prob": 0.85,
                "syntax_enable": True,
            },
        )
    )
    counts = Counter(e.road_class for e in artifact.roads.edges)
    assert counts["arterial"] > 0
    assert counts["collector"] > 0
    assert counts["local"] > 0
    assert counts["collector"] + counts["local"] >= counts["arterial"] * 4
    assert artifact.metrics.road_edge_count_by_class.get("collector", 0) == counts["collector"]
    assert artifact.metrics.road_edge_count_by_class.get("local", 0) == counts["local"]
    assert artifact.metrics.illegal_intersection_count <= max(artifact.metrics.road_edge_count * 2, 50)
    assert artifact.metrics.intersection_t_junction_count >= 1.0
    assert artifact.metrics.local_culdesac_edge_count_final > 0.0
    assert artifact.metrics.local_culdesac_preserved_ratio > 0.0
    assert any("Collector generator: classic_turtle" in n or "Collector generator: grid_clip" in n for n in artifact.metrics.notes)
    assert any("Local generator:" in n for n in artifact.metrics.notes)
