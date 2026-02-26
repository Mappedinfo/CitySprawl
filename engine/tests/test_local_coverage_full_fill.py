from collections import Counter

from engine.generator import generate_city
from engine.models import GenerateConfig


def test_local_roads_prioritize_coverage_on_10km_extent():
    artifact = generate_city(
        GenerateConfig(
            seed=29,
            extent_m=10000.0,
            grid_resolution=72,
            hubs={"t1_count": 1, "t2_count": 4, "t3_count": 14, "min_distance_m": 320.0},
            roads={
                "k_neighbors": 4,
                "loop_budget": 3,
                "branch_steps": 2,
                "slope_penalty": 2.0,
                "river_cross_penalty": 260.0,
                "style": "mixed_organic",
                "collector_spacing_m": 480.0,
                "local_spacing_m": 160.0,
                "collector_jitter": 0.12,
                "local_jitter": 0.16,
                "local_generator": "classic_sprawl",
                "collector_generator": "classic_turtle",
                "local_geometry_mode": "classic_sprawl_rerouted",
                "local_reroute_coverage": "selective",
                "local_reroute_min_length_m": 55.0,
                "local_classic_probe_step_m": 20.0,
                "local_classic_seed_spacing_m": 140.0,
                "local_classic_continue_prob": 0.72,
                "local_classic_culdesac_prob": 0.70,
                "local_classic_branch_prob": 0.12,
                "river_setback_m": 16.0,
                "minor_bridge_budget": 4,
                "max_local_block_area_m2": 220000.0,
                "classic_seed_spacing_m": 320.0,
                "classic_probe_step_m": 24.0,
                "classic_branch_prob": 0.40,
                "classic_continue_prob": 0.92,
                "syntax_enable": True,
            },
        )
    )
    counts = Counter(e.road_class for e in artifact.roads.edges)
    metrics = artifact.metrics

    assert artifact.terrain.extent_m == 10000.0
    assert counts["minor_local"] > 0
    assert metrics.local_reroute_applied_count >= 1
    assert metrics.local_two_point_edge_ratio < 0.95
    assert metrics.local_buildable_area_m2 is not None
    assert metrics.local_buildable_area_m2 > 0.0
    assert metrics.local_coverage_radius_m is not None
    assert metrics.local_coverage_radius_m >= 90.0
    assert metrics.local_coverage_ratio is not None
    assert metrics.local_coverage_ratio >= 0.90
    assert metrics.local_frontier_supplement_added_count is not None
    assert metrics.local_frontier_supplement_added_count > 0
    assert metrics.local_coverage_supplement_added_count is not None
    assert metrics.local_coverage_supplement_added_count >= metrics.local_frontier_supplement_added_count
    assert metrics.local_uncovered_area_m2 is not None
    assert metrics.local_uncovered_area_m2 >= 0.0
    assert metrics.local_uncovered_area_m2 < metrics.local_buildable_area_m2
    assert metrics.local_classic_stop_road_too_far_count == 0
    assert any("Local road coverage (buildable area):" in note for note in metrics.notes)
